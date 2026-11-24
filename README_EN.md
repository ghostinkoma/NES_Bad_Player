# NES_BadPlayer

A full-screen (256×240) video player with sound for the NES / Famicom.
The video is stored in the ROM and played back with audio by a stock 6502 program — no extra chips.

[日本語の README はこちら](README.md)

## Download
The distributable sample ROM is on the [Releases page](https://github.com/ghostinkoma/NES_Bad_Player/releases/tag/v1.0-sample):
`NES_BadPlayer_sample_pcm.nes` (8 MB).

It contains:
- **OPENING** – the intro
- **PUNINEKO** – the menu background animation, with music
- **BAD APPLE** – Bad Apple!! (black and white, 30 fps, about 3 min 39 s)

## Highlights (PCM sample ROM)
- **Full-screen video**: 256×240, 1-bit black and white, 30 fps (the menu animation runs at 60 fps).
- **7-bit PCM audio at 18.6 kHz**: the CPU writes `$4011` every 96 cycles, all the time — even while it streams
  video data to the PPU. A small correction for the APU's nonlinear mixer is applied to the samples.
- **Cycle-exact player** (3 KB, copied into every bank): every code path takes exactly 96 cycles between two `$4011` writes.
  No NMI, no IRQ, no DMC.
- **Frame sync with sprite 0**: a fixed tile in the bottom-right cell plus sprite 0 gives a hit on line 239 every frame.
  This tile also shows the real picture, so nothing looks different on screen.
- **Pause, frame step forward/back, FPS display**, and three play modes (REPEAT / STOP / SEQ).
- The menu background loops seamlessly (the screen is never blanked between loops).
- **Mapper**: BNROM (mapper 34), NES 2.0 header, 8 MB (256 × 32 KB banks).

## Tested on
- [puNES](https://github.com/punesemu/puNES) and [Mesen](https://www.mesen.ca/) 2.2.1.
- Real hardware: **not tested yet**. An 8 MB BNROM board is unusual, so you need a flash cart that supports it.

## Controls
| Screen | Button | Action |
|---|---|---|
| Opening | START | Skip to the menu |
| Menu | Up / Down, A | Choose a video / Play |
| | SELECT | Change play mode (REPEAT → STOP → SEQ) |
| Playing | B | Pause / Resume |
| | Right / Left (while paused) | Step one frame forward / back |
| | SELECT | FPS display on / off |
| | START | Back to the menu |

## How it works (short version)
One frame of playback:
1. **While the picture is being drawn**: copy the next video record from ROM into RAM, read ahead the audio samples that will
   be needed during VBlank, then wait for the sprite-0 hit. Every step is a 96-cycle "slot" that also writes one audio sample.
2. **VBlank**: up to 19 write slots. Each slot plays one sample and makes 10 PPU writes (address + data, in one of 5 fixed layouts).
   The slots are chained with `rts`: each record stores the addresses of the slot routines to run next.
3. **After VBlank**: 9 bookkeeping slots (joypad, frame counting, FPS, bank switching, next-record setup).

The encoder (`tools/video/make_pcm_rom.py`) picks which tiles and name-table cells to update each frame
within the write budget, and lays out video records and audio pages so that each 32 KB bank holds both.
Average difference from the source frames: about 0.9 % of pixels for Bad Apple!!.

More details (in Japanese): [docs/HISTORY.md](docs/HISTORY.md) (stages 8–10c), [docs/BUGFIX.md](docs/BUGFIX.md).

## Building
Requirements: [cc65](https://cc65.github.io/) (`ca65.exe`, `ld65.exe` in `..\bin\`), Python 3 with Pillow and numpy 2.x,
ffmpeg, and Mesen 2.2.1 (`..\Mesen.exe`, used for the automatic test). Scripts are PowerShell.

```powershell
.\build_pcm_video.ps1 -Test
```

- Output: `build\pcmv\pcm_video.nes`. `-Test` runs Mesen headless and checks the `$4011` write timing,
  the screen contents and the audio against the ROM data.
- The video and music source files (`resouce\`) are **not** in this repository. Prepare your own and set the paths
  in `tools\video\titles_pcm.json`.
- The older DPCM version (more titles, gray/color modes): `.\build_video.ps1`.

## License
Following the spirit of the Bad Apple!! fan works, the program is free to use, modify and redistribute
for **non-commercial purposes only**. Please do not use it commercially.

All rights to the video and music belong to their respective owners.
The material in the sample ROM is included with permission from the people who provided it.
This software is provided "as is", without warranty of any kind.
