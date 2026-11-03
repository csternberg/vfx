# CLAUDE.md

Context for Claude Code (or any future assistant) working in this repo.
Read this before changing `vfx.py`. Several choices below work around
non-obvious ffmpeg behaviour, and each one fixed a real bug in v1, so
they are easy to undo by accident while refactoring.

## What this project is

`vfx.py` is a single-file Python CLI (stdlib only) that wraps `ffmpeg` and
`ffprobe` to extract still images from video files. It has three
selection modes:

- `-N x`: x evenly spaced frames (default 16)
- `-T x`: one frame every x seconds
- `-N 0` / `-T 0`: every frame

It also supports resizing (`-X`/`-Y`), image format and quality
(`-F`/`-Q`), timestamp file names (`-M`), per-file prompting (`-P`),
saving next to the video (`-D`), dry run (`-L`) and recursive discovery
(`-R`).

v1 was written with Microsoft Copilot. v2.0.0 is a rewrite that fixed
the crashes and speed problems listed in the README's "Changes in 2.0.0".
`README.md` is the user-facing reference: keep its switch table in sync
with `build_parser()`. `vfx.1` is the man page; keep it in sync too
(switches, exit codes, version in its `.TH` line).

## Architecture at a glance

- **CLI**: `build_parser()` uses argparse with `add_help=False` (so
  `-H`/`-h`/`-?`/`--help` can share one help action) and
  `allow_abbrev=False`. The help text is generated from the argparse
  definitions, so don't reintroduce a hand-written help string. Two
  mutually exclusive groups: `-S`/`-I`/`--verbose` and `-N`/`-T`. `-N` defaults to
  `None`, not 16, so "not given" can be told apart from "given as 16".
  `resolve_mode()` turns the arguments into `("count", n)`,
  `("interval", s)` or `("all", None)`.
- **Case-insensitive switches**: `normalize_argv()` upper-cases
  single-letter switches (`-n` becomes `-N`) before argparse sees them,
  because argparse can't do this itself. Long options stay lowercase.
  This means every short switch must be unique ignoring case, so `-v`
  is version (`-V`), and `--verbose` has no short form. Combined
  switches such as `-rs` are not supported.
- **Verbosity**: `SILENT` / `INFO` / `VERBOSE`, stored on `args.verbosity`.
  INFO is the default; `-I` is just an explicit spelling of it, and
  `--verbose` selects VERBOSE (ffmpeg's own output). All of the script's
  own output goes through `Reporter`: `say` (stdout), `error` (stderr)
  and `progress` (INFO mode only, one line per video redrawn in place
  with `\r`: `name: generated x of y frames (i of n videos)`). Progress
  comes from `show()` in `process_video()`: `-N` reports per finished chunk of frames, and
  stream mode gets ffmpeg's `frame=` lines via `run_ffmpeg(on_frame=…)`.
  The "of y" in stream mode is an estimate; the final line uses the
  actual count.
- **Discovery**: `find_videos()` collects the *complete* list before any
  extraction. That's deliberate: it keeps `-R` from walking into
  `_frames` folders created during the run. Files are matched by
  extension (any case, `VIDEO_EXTENSIONS`, about 40 common video
  formats), and macOS `._*` files are skipped. Unknown extensions are
  skipped in folder scans on purpose (probing every file with ffprobe
  was rejected as slow). A file named explicitly on the command line is
  accepted whatever its extension. `-W mask` filters folder scans by file
  name (`fnmatch`, case-insensitive) inside `find_videos()`, so output
  placement (`-D`, `-O`) is unaffected; it is ignored for an explicit file.
  The program does no wildcard expansion of the source itself.
- **`-O` output root**: `unique_output_root()` picks `frames`, `frames_001`,
  and so on once per run (in `main()`, after discovery). `output_dir_for()`
  then places each video at `<root>/<path relative to the source dir>/
  <stem>_frames`; with `-D` the images of each source folder share
  `<root>/<relative path>` directly. `confirm_output_name()` asks y/n when
  the last component of the `-O` value contains a `.` (skipped in dry run;
  EOF counts as no; only `y`/`yes` proceeds). It runs after
  `unique_output_root()` and shows the final folder name when it differs
  from the typed one. Its value is optional
  (`nargs="?"`), which makes `vfx -o movie.mp4` take the video as the
  folder name; this is documented, not fixed. A failed file removes the
  empty folders it created, up to the root (`remove_empty_parents()`).
- **Probing**: `probe()` runs ffprobe once per file (JSON output) and
  returns `(duration, estimated_frames)`. Dry run never probes, and
  needs neither ffmpeg nor ffprobe.
- **Extraction paths**:
  - `extract_by_count()` (`-N`) seeks to each sample with `-ss T -i file`
    (`-ss` before `-i` seeks the input) and writes one frame per output
    (`-map k:V:0 -frames:v 1 -update 1`). Samples go in chunks of
    `BATCH_SIZE` (16) inputs per ffmpeg process, and up to `MAX_WORKERS`
    (4) chunks run at once in a thread pool; `--verbose` runs them one at
    a time so ffmpeg's output isn't interleaved. Progress is reported
    from the main thread only (`Reporter` isn't thread safe). On a 10-min
    720p clip this took `-N 100` from 23 s to 5 s; batch sizes 8 to 32
    are about equal, and more workers were not tried.
  - `extract_stream()` (`-T` and all frames) is a single ffmpeg pass
    over the whole video.
- **Output naming**:
  - `output_dir_for()` picks `<stem>_frames`, `_frames_1`, and so on.
  - `unique_prefix()` picks `<stem>`, `<stem>_1`, and so on, so that
    `-D` runs never collide with existing images.
  - `remove_partial()` and `count_images()` both match
    `^<prefix>_[\d.-]+\.<ext>$`. Change them together.
- **Errors**: `VfxError` is a per-file problem. `main()` catches
  `Exception` per file, reports it and continues. `KeyboardInterrupt`
  is deliberately *not* an `Exception`: it passes through to the
  `__main__` wrapper, which exits with 130. `process_video()` catches
  `BaseException` only to clean up partial images, then re-raises.
  `UserAbort` (`a` at the `-P` prompt, or EOF on stdin) ends the run
  cleanly.
- **Exit codes**: 0 success, 1 at least one file failed (or bad
  source, or ffmpeg missing), 2 argparse usage error, 130 Ctrl-C.

## ffmpeg gotchas handled here (do not regress these)

1. **`-N` must seek; it must not decode the whole video.** v1 used
   `fps=1/interval`, which decodes every frame to keep 16. The
   replacement for its "fallback" path was even worse: `select='gte(t,…)'`
   per frame, decoding from the start every time. The seek-based
   approach was 2× faster and used 6× less CPU on a 10-minute 720p
   clip, and the gap grows with length. Keep `-ss` **before** `-i`.
   Put after `-i`, it becomes a decode-and-discard seek and the speed
   gain disappears.
2. **Single images need `-update 1`.** Recent ffmpeg (8.0 tested)
   complains when an image2 output name has no `%d` pattern, unless
   `-update 1` is given.
3. **`-T` uses a bucket `select`, not `fps=1/x`.** The `fps` filter drops
   the frame at the last interval mark (10 s at `-T 3` gave 0/3/6, not
   0/3/6/9). The filter used is
   `select=isnan(prev_selected_t)+gt(floor(t/x)\,floor(prev_selected_t/x))`,
   which keeps the first frame of each interval and doesn't drift. The
   comma inside the expression **must** stay escaped as `\,`, or
   ffmpeg's filtergraph parser splits the filter there. It needs
   `-fps_mode vfr`, or the image2 muxer duplicates frames to fill the
   gaps. All-frames mode uses `-fps_mode passthrough`.
4. **Stream duration can be `N/A`.** `.mkv` and `.webm` report duration
   only at container (format) level. `probe()` tries the stream duration
   first, then the format duration. `_to_float()` maps `N/A`, 0,
   negative and non-finite values to `None`. Never call `float()` on raw
   ffprobe output: that was a v1 crash.
5. **Hidden overwrite prompt and stdin.** Without `-nostdin`, ffmpeg asks
   "Overwrite? [y/N]" on stderr and reads stdin. In `-S` mode that
   question is invisible and the run hangs. With `-P`, ffmpeg can also
   swallow the user's y/n/a keystrokes. `run_ffmpeg()` always passes
   `-nostdin -n` (never overwrite). Note that `-n` doesn't protect
   `%03d` pattern outputs, because ffmpeg only checks the literal
   pattern name. `unique_prefix()` is what actually prevents collisions.
6. **Cover art.** MP4/M4V files can have an attached-picture (cover art)
   stream, which ffmpeg may pick as "the" video stream. Always select
   the video with **capital** `V` (`-map 0:V:0`, and
   `-select_streams V:0` in ffprobe), which excludes attached pictures.
7. **Pipe deadlock.** In INFO/SILENT mode `run_ffmpeg()` merges stderr
   into stdout (`stderr=subprocess.STDOUT`) and drains that single pipe
   continuously. Never open `stdout=PIPE` and `stderr=PIPE` as two pipes
   with `Popen` while reading only one. Once the unread pipe fills,
   ffmpeg blocks and the run freezes. (`video_blur` shipped exactly this
   bug once.) The `-progress pipe:1` key=value lines are kept out of the
   error-tail buffer via `_PROGRESS_KEYS`. Extend that set if you parse
   more progress keys.
8. **Timestamp names use real frame times.** For `-M` in stream mode,
   ffmpeg writes files named by pts in milliseconds
   (`-enc_time_base 1:1000 -frame_pts 1`, pattern `<prefix>_%d`), which
   are then renamed to `HH-MM-SS.mmm`. This stays correct for VFR video
   and when intervals are shorter than a frame. Don't replace it with
   `k * interval` arithmetic. In `-N` mode the name is the requested
   seek time, and the image is the frame on screen at that moment.
9. **Pixel format.** Don't force `-pix_fmt yuv420p` for JPEG (v1 did).
   Let the encoder negotiate; it picks full-range `yuvj*`.
10. **`-fps_mode` needs ffmpeg ≥ 5.1.** The older `-vsync` spelling is
    deprecated. If support for older ffmpeg is ever needed, detect the
    version rather than switching back unconditionally.

## Testing

- `python3 -m py_compile vfx.py test_vfx.py` after every edit: cheap,
  so run it first.
- `python3 -m unittest -v test_vfx` runs 35 tests, taking about 10 s
  with ffmpeg 8.0 on macOS (about 20 s on Windows with ffmpeg 7.1).
  - `HelperTests` are pure unit tests.
  - `CliTests` run the script as a subprocess against synthetic
    `testsrc`/`sine` clips generated in a temp folder, and are skipped
    when ffmpeg is missing.
  - Each v1 crash has a regression test there. Add one for every bug
    you fix.
- **Always use a timeout** when running the script or ffmpeg during
  development. The tests pass `timeout=` to every `subprocess.run`.
  macOS has no `timeout` command, so wrap ad-hoc shell runs with
  `perl -e 'alarm 60; exec @ARGV' python3 vfx.py …`.
- Benchmarks: compare wall *and* CPU time (`/usr/bin/time -p`) on a
  video of at least several minutes. On short clips, process start-up
  dominates and hides the difference.

## Style / conventions

- Single file, standard library only, argparse, no external CLI
  framework. Must keep working on Windows: the tool's history suggests
  Windows users, so no POSIX-only calls. Use `pathlib` and
  `shutil.which`.
- All ffmpeg calls go through `run_ffmpeg()`. Don't add ad-hoc
  `subprocess` calls for ffmpeg elsewhere.
- Output is never overwritten. New runs get new folder names or
  prefixes.
- Single-letter upper-case switches, matching v1 (accepted in either case,
  see `normalize_argv()`). Every switch has a lowercase long alias
  (`--recursive`, `--count`, `--same-dir`, `--dry-run`, and so on); the
  one exception is `--verbose`, which is long-only.
- **Versioning (the owner wants this kept up to date):** the current
  version is 2.5.0. Whenever you make a user-visible change, bump
  `__version__` (minor for new features or behaviour changes, patch for
  fixes), the README heading, a new "Changes in x.y.z" README section,
  and the `vfx.1` `.TH` line, all together. Also update the docs
  (README, `vfx.1`, this file) and the tests in the same change.

## Open items / possible future work

The project is licensed under GPL v3 (`LICENSE`); the README says the same.

- `-N` workers are capped at 4; `-T`/all-frames mode is a single ffmpeg
  pass and could be split by time range, but ffmpeg already threads it.
- If `-N` is larger than the video's frame count, some images are
  duplicates, and with `-M` they could share a millisecond name (ffmpeg
  then fails because of `-n`). There's no warning or cap yet.
- No parallelism across files.
- No `.gitattributes` or CI. A GitHub Actions workflow that installs
  ffmpeg and runs the unittest suite would be cheap to add.
