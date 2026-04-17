"""Patch _store_image to use unique filenames."""
import pathlib

target = pathlib.Path(__file__).parent.parent / "core" / "media" / "image_quality.py"
content = target.read_bytes()

old = (
    b"    def _store_image(self, data: bytes, title: str) -> str:\r\n"
    b'        safe = re.sub(r"[^a-zA-Z0-9\\s-]", "", title)[:40].strip().replace(" ", "_") or "image"\r\n'
    b'        path = self.download_dir / f"{safe}.jpg"\r\n'
    b"        path.write_bytes(data)\r\n"
    b"        return str(path.resolve())\r\n"
)
new = (
    b"    def _store_image(self, data: bytes, title: str) -> str:\r\n"
    b"        import time as _time\r\n"
    b'        safe = re.sub(r"[^a-zA-Z0-9\\s-]", "", title)[:40].strip().replace(" ", "_") or "image"\r\n'
    b"        ts_ms = int(_time.time() * 1000)\r\n"
    b"        img_hash = hashlib.sha1(data[:4096]).hexdigest()[:8]\r\n"
    b'        path = self.download_dir / f"{safe}_{ts_ms}_{img_hash}.jpg"\r\n'
    b"        path.write_bytes(data)\r\n"
    b"        return str(path.resolve())\r\n"
)

if old in content:
    patched = content.replace(old, new, 1)
    target.write_bytes(patched)
    print("PATCHED OK")
else:
    print("NOT FOUND - dumping context:")
    idx = content.find(b"def _store_image")
    print(repr(content[idx : idx + 300]))
