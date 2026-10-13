"""检查恢复后文件的真实字节：编码、换行、行尾 BOM。"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(__file__).resolve().parents[2]   # 脚本已归档到 _recovered/created_by_agent/
REC = BASE / "_recovered"

for name in ("login.py", "search.py"):
    raw = (BASE / name).read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    print("=" * 60)
    print(f"{name}: {len(raw)} 字节")
    print(f"  前 16 字节: {raw[:16]!r}")
    print(f"  后 16 字节: {raw[-16:]!r}")
    print(f"  \\r\\n={crlf}  \\n={lf}  (纯LF则 crlf=0, lf=行数-1)")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        print("  ** 含 BOM **")
    for enc in ("utf-8", "utf-8-sig", "utf-16", "gbk"):
        try:
            t = raw.decode(enc)
            print(f"  decode({enc:10s}) OK, {len(t)} 字符, "
                  f"行数 {len(t.splitlines())}")
            break
        except Exception as e:
            print(f"  decode({enc:10s}) 失败: {type(e).__name__}")
    src = (REC / f"original_{name}").read_bytes()
    print(f"  源文件 original_{name}: {len(src)} 字节, "
          f"\\r\\n={src.count(chr(13).encode()+chr(10).encode())}, "
          f"BOM={src[:2] in (bytes([255,254]), bytes([254,255]))}")
    print(f"  两者字节完全一致: {raw == src}")
