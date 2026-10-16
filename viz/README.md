# Charts

Drawn from real runs with `python viz/make_charts.py`:

- `timelines.png`: the same $640 refund in stages 1, 2 and 3 ([`data/`](data/) holds the traced events, exported from the platform's `/runs`)
- `context.png`: tokens each model call reads, loop vs graph
- `reliability.png`: 0.99ⁿ
- `evaluation.png`: loop vs graph on the 12 cases ([`../06-reliability/results/`](../06-reliability/results/))
- `viewer.png`: the live viewer at http://127.0.0.1:8700/ui during stage 3

## Watermark

The images and videos used in the write-up carry `medium.com/@brahimg` in a thin strip under the picture (`python viz/watermark.py`; done files are listed in `watermarked.txt`). After redrawing a chart or GIF, remove its line from `watermarked.txt` and run the script again.
