# Examples

- **[quickstart.md](quickstart.md)** — drive a full phase from `init` to `close`
  with the CLI, and wire the hooks into a project.
- **[review_demo.py](review_demo.py)** — a runnable script showing the default
  static reviewer flagging issues, and the cloud reviewer staying inert with no
  sender wired (no network).

Run the demo from a checkout:

```bash
pip install -e .
python examples/review_demo.py
```
