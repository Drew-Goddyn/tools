import AppKit
import Foundation

struct Update: Decodable {
    let version: Int
    let phase: String
    let filename: String
    let total_frames: Int?
    let frame: Int?
    let percent: Int?
    let phase_started_at: Double
    let updated_at: Double
    let frame_advanced_at: Double?
}

final class ProgressMenu: NSObject, NSMenuDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private let fileRow = NSMenuItem()
    private let phaseRow = NSMenuItem()
    private let frameRow = NSMenuItem()
    private let elapsedRow = NSMenuItem()
    private let advanceRow = NSMenuItem()
    private let output: URL
    private var latest: Update?
    private var phaseStarted = Date()
    private var lastAdvance: Date?
    private var staleCheck: DispatchWorkItem?
    private var menuClock: Timer?
    private var buffer = Data()

    init(output: URL) {
        self.output = output
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
        item.menu = menu
        if let button = item.button {
            button.image = NSImage(systemSymbolName: "film", accessibilityDescription: "Screen recording cleaner")
            button.imagePosition = .imageLeading
            button.font = .monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
            button.title = " …"
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
        phaseRow.title = update.phase + (update.percent.map { " · \($0)% of this step" } ?? "")
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
        let title = update.percent.map { " \($0)%" }
            ?? (update.phase.hasPrefix("Waiting") || update.phase == "Compressing" ? " …" : " Check")
        item.button?.title = title
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

    func finish() {
        staleCheck?.cancel()
        menuClock?.invalidate()
        NSStatusBar.system.removeStatusItem(item)
        NSApplication.shared.terminate(nil)
    }
}

guard CommandLine.arguments.count == 2 else { exit(64) }
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = ProgressMenu(output: URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true))
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
