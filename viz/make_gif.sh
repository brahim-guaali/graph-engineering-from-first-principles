#!/bin/zsh
# Assemble frames (f000.png, f001.png, ...) captured from a replayed real run into a GIF and an MP4.
#   viz/make_gif.sh <frames dir> <out name without extension> [fps] [width]
set -eu
dir=$1; out=$2; fps=${3:-10}; width=${4:-960}
last=$(ls $dir/f*.png | tail -1)
n=$(ls $dir/f*.png | wc -l | tr -d ' ')
for i in $(seq 1 $((fps * 3))); do cp $last $dir/f$(printf %03d $((n + i - 1))).png; done   # hold the end 3 s
ffmpeg -loglevel error -y -framerate $fps -i $dir/f%03d.png \
  -vf "scale=$width:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  -loop 0 $out.gif
ffmpeg -loglevel error -y -framerate $fps -i $dir/f%03d.png -vf "scale=$width:-2:flags=lanczos,format=yuv420p" \
  -c:v libx264 -crf 24 -movflags +faststart $out.mp4
ls -la $out.gif $out.mp4 | awk '{print $5, $9}'
