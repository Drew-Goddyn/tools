import AppKit
import Foundation

struct Update: Decodable {
    let version: Int
    let phase: String
    let filename: String
    let step: Int?
    let total_steps: Int?
    let total_frames: Int?
    let frame: Int?
    let percent: Int?
    let phase_started_at: Double
    let updated_at: Double
    let frame_advanced_at: Double?
}

final class ProgressMenu: NSObject, NSMenuDelegate {
    private let item: NSStatusItem
    private let menu = NSMenu()
    private let fileRow = NSMenuItem()
    private let phaseRow = NSMenuItem()
    private let frameRow = NSMenuItem()
    private let elapsedRow = NSMenuItem()
    private let advanceRow = NSMenuItem()
    private let pauseRow = NSMenuItem()
    private let output: URL
    private let launchdTarget: String?
    private var pausing = false
    private var inputEnded = false
    private var latest: Update?
    private var phaseStarted = Date()
    private var lastAdvance: Date?
    private var staleCheck: DispatchWorkItem?
    private var menuClock: Timer?
    private var buffer = Data()

    init(output: URL, launchdTarget: String? = nil) {
        self.output = output
        self.launchdTarget = launchdTarget
        // AppKit's autosaved position preference is undocumented. Register only
        // an initial default near the clock; a user's saved position wins.
        UserDefaults.standard.register(defaults: ["NSStatusItem Preferred Position RecordingProgress": 0])
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.autosaveName = "RecordingProgress"
        super.init()
        menu.autoenablesItems = false
        menu.delegate = self
        for row in [fileRow, phaseRow, frameRow, elapsedRow, advanceRow] {
            row.isEnabled = false
            menu.addItem(row)
        }
        menu.addItem(.separator())
        let open = NSMenuItem(title: "Open finished recordings", action: #selector(openOutput), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        if launchdTarget != nil {
            pauseRow.title = "Pause processing"
            pauseRow.action = #selector(pauseProcessing)
            pauseRow.target = self
            menu.addItem(pauseRow)
        }
        item.menu = menu
        if let button = item.button {
            button.image = NSImage(systemSymbolName: "film", accessibilityDescription: "Screen recording cleaner")
            button.imagePosition = .imageOnly
        }
    }

    func read(_ data: Data) {
        buffer.append(data)
        while let end = buffer.firstIndex(of: 10) {
            let line = Data(buffer[..<end])
            buffer.removeSubrange(...end)
            if let update = try? JSONDecoder().decode(Update.self, from: line), update.version == 1 {
                apply(update)
            }
        }
    }

    private func apply(_ update: Update) {
        let changed = update.phase_started_at != latest?.phase_started_at
        if changed {
            phaseStarted = Date(timeIntervalSince1970: update.phase_started_at)
            lastAdvance = nil
            staleCheck?.cancel()
        }
        lastAdvance = update.frame_advanced_at.map { Date(timeIntervalSince1970: $0) }
        if update.frame != nil && (changed || latest?.frame == nil || update.frame_advanced_at != latest?.frame_advanced_at) {
            staleCheck?.cancel()
            let check = DispatchWorkItem { [weak self] in self?.render() }
            staleCheck = check
            // One follow-up while frames are expected, cancelled on each advance.
            let delay = max(0, 60 - Date().timeIntervalSince(lastAdvance ?? phaseStarted))
            DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: check)
        }
        latest = update
        render()
    }

    private func elapsed(_ since: Date) -> String {
        let seconds = max(0, Int(Date().timeIntervalSince(since)))
        return seconds < 60 ? "\(seconds)s" : "\(seconds / 60)m \(seconds % 60)s"
    }

    private func render() {
        guard let update = latest else { return }
        fileRow.title = update.filename.count <= 100 ? update.filename
            : String(update.filename.prefix(64)) + "…" + String(update.filename.suffix(28))
        let step = update.step.flatMap { step in
            update.total_steps.map { "Step \(step) of \($0) · " }
        } ?? ""
        phaseRow.title = step + update.phase + (update.percent.map { " · \($0)%" } ?? "")
        frameRow.isHidden = update.frame == nil
        if let frame = update.frame {
            frameRow.title = update.total_frames.map { "Frames: \(frame) of \($0)" } ?? "Frames: \(frame)"
        }
        elapsedRow.title = "Time in this step: \(elapsed(phaseStarted))"
        advanceRow.isHidden = update.frame == nil
        if let advanced = lastAdvance {
            advanceRow.title = Date().timeIntervalSince(advanced) >= 60
                ? "No frame advance for \(elapsed(advanced))"
                : "Last frame advance: \(elapsed(advanced)) ago"
        } else {
            advanceRow.title = Date().timeIntervalSince(phaseStarted) >= 60
                ? "No output frames for \(elapsed(phaseStarted))"
                : "Waiting for the first frame"
        }
        item.button?.toolTip = "\(update.filename)\n\(phaseRow.title)"
    }

    func menuWillOpen(_ menu: NSMenu) {
        render()
        menuClock?.invalidate()
        let clock = Timer(timeInterval: 1, repeats: true) { [weak self] _ in self?.render() }
        menuClock = clock
        RunLoop.main.add(clock, forMode: .common)
    }

    func menuDidClose(_ menu: NSMenu) {
        menuClock?.invalidate()
        menuClock = nil
    }

    @objc private func openOutput() { NSWorkspace.shared.open(output) }

    private func launchctl(_ arguments: [String], completion: @escaping (Int32) -> Void) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { process in
            DispatchQueue.main.async { completion(process.terminationStatus) }
        }
        do { try process.run() } catch { completion(-1) }
    }

    @objc private func pauseProcessing() {
        guard let target = launchdTarget, !pausing else { return }
        pausing = true
        pauseRow.title = "Pausing…"
        pauseRow.isEnabled = false
        // Persist the pause before stopping the job and all of its child processes.
        launchctl(["disable", target]) { [self] code in
            guard code == 0 else {
                pauseFailed("macOS couldn't pause automatic processing. Please try again.")
                return
            }
            launchctl(["bootout", target]) { [self] code in
                if code == 0 {
                    pausing = false
                    finish()
                } else {
                    pauseFailed("Automatic processing is paused, but macOS couldn't stop the current batch.")
                }
            }
        }
    }

    private func pauseFailed(_ message: String) {
        pausing = false
        pauseRow.title = "Pause processing"
        pauseRow.isEnabled = true
        let alert = NSAlert()
        alert.messageText = "Couldn't finish pausing"
        alert.informativeText = message + " Your originals are safe."
        alert.runModal()
        if inputEnded { finish() }
    }

    func finish() {
        inputEnded = true
        if pausing { return } // Finish the user's pause even if the worker exits first.
        staleCheck?.cancel()
        menuClock?.invalidate()
        // Termination removes the item. Explicit removal clears its saved position.
        NSApplication.shared.terminate(nil)
    }
}

guard (2...3).contains(CommandLine.arguments.count) else { exit(64) }
let target = CommandLine.arguments.count == 3 ? CommandLine.arguments[2] : nil
if let target, !target.hasPrefix("gui/\(getuid())/local.screen-recording-cleaner") { exit(64) }
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = ProgressMenu(output: URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true), launchdTarget: target)
let input = FileHandle.standardInput
input.readabilityHandler = { handle in
    let data = handle.availableData
    if data.isEmpty {
        handle.readabilityHandler = nil
        DispatchQueue.main.async { controller.finish() }
    } else {
        DispatchQueue.main.async { controller.read(data) }
    }
}
app.run()
