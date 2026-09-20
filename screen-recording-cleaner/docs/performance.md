# Processing speed

Measured on an Apple M2 Pro with 32 GB RAM on September 20, 2026. The inputs were two roughly 6.5-second excerpts of real built-in macOS recordings, both 3022 × 1622 at approximately 60 frames per second. They contain moving 3D scenes and small interface text. Originals were read only; benchmark outputs were kept separate from the installed queue.

| Excerpt | Original | Previous processing | Faster processing | Previous output | Faster output |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 48.13 MB | 25.72 s | 9.66 s | 8.39 MB | 8.14 MB |
| B | 53.05 MB | 20.44 s | 7.18 s | 5.29 MB | 5.26 MB |

These times cover inspection, compression, validation, and publication in an isolated local output folder. They exclude the unchanged 30-second wait for a recording to finish. The previous worker was taken from commit `83c683c`. Both versions used CRF 18 and four encoder threads. Each complete run was measured once, sequentially, in the foreground; other applications and the installed worker were still running. The figures demonstrate these samples, not a promised multiplier for every recording.

## macOS job scheduling

The installed job's scheduling policy had a larger effect than the encoder preset. Excerpt A was also tested in isolated real launchd jobs:

| Pipeline and scheduling | Complete processing |
| --- | ---: |
| Previous worker, Background class, low-priority disk access | 92.48 s |
| Faster worker, same Background class and disk priority | 57.89 s |
| Faster worker, Standard class, CPU nice level 10, low-priority disk access | 9.51 s |

The final setting was about 9.7 times faster than the old service on this sample. Each faster-worker run produced an 8.14 MB output. Each combination was measured once; concurrent activity can affect the result. This does not establish a universal speedup.

The installer now uses the Standard class with nice level 10. It still gives foreground work higher CPU priority, keeps low-priority disk access, and uses four encoder threads by default. More CPU capacity is available during processing; the job still exits when finished. Apple's [launchd settings](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5) describe these separate process-class, CPU-priority, and disk-priority controls. No measurements of foreground application latency or battery energy were made.

A complete 33.53-second, 208.95 MB recording also passed through the faster worker in a temporary launchd job using these settings. Inspection, encoding, verification, and publication took **49.34 seconds**, producing **32.39 MB**. The original's hash was unchanged. This was a private test copy; it did not alter the installed queue. There is no equivalent timed full-file baseline for this measurement.

## What changed

The encoder uses the `fast` software preset instead of `slow`. The original is decoded once during encoding, where FFmpeg records every input frame. The finished copy is decoded once to check playback, audio, frame count, and frame timing. The previous pipeline decoded the original separately to count frames, then decoded the output twice. Exact copies now need a byte comparison and one decode.

The first menu step reads metadata instead of counting every frame before compression. On these excerpts it took about 0.15 seconds. Step numbers and progress during compression and playback verification remain visible.

## Quality and the hardware comparison

Whole-frame SSIM against the original was 0.9927 → 0.9915 for A and 0.9949 → 0.9941 for B. SSIM is one similarity measure, not proof that all content looks identical. These remain lossy encodes; resolution, frame timing, and audio preservation are checked separately. The quality setting and soft 20 MB target are unchanged.

A native-resolution crop of the small interface text in excerpt A was compared visually with the original and the previous encode. The text remained readable with no obvious additional blur in that crop; this is a limited visual check, not a claim of lossless output.

Apple's H.264 hardware encoder was tested too. At quality 70 it encoded the excerpts in about 4.8–4.9 seconds, but produced 40.84 MB and 25.39 MB files, with SSIM 0.9928 and 0.9934. A lower hardware quality setting reduced size at a larger similarity loss. The software `fast` preset gave a better combination of speed, size, and quality for these recordings, so hardware encoding is not enabled.

## Verification

Tests exercise actual FFmpeg output with variable frame timing, copied AAC and ALAC audio, converted PCM audio, corrupt media, deliberately missing frames, and changed timestamps. Frame-count evidence comes from decoded frames, not just container metadata. See [FFmpeg's per-frame statistics](https://ffmpeg.org/ffmpeg.html#Advanced-options) for the mechanism.

The release passed 55 media/installer tests and eight native menu/launchd tests. Independent review covered validation, termination, installation, and the scheduling change. The native tests select actual menu actions programmatically; they do not establish a physical click or a Finder double-click.

After installation, a uniquely named copy of excerpt A triggered automatic delivery from the configured recording folder to Downloads: 48.13 MB became 8.14 MB, with all 390 frames at 3022 × 1622 independently verified. Delivery took 46.11 seconds, including the unchanged 30-second stability wait; observed processing took 15.45 seconds. This was a real installed folder-trigger test using an existing recording, not a fresh keyboard capture. The 120 pre-upgrade history entries and configuration were preserved, and only the temporary test source and output were removed afterward.

Pause terminates the worker and encoder. It does not preserve a suspended encoder in RAM. An interrupted recording still starts over on resume; this update reduces the work required each time. Disk checkpoints within an encode are outside this change.
