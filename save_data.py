import json
from pathlib import Path
import re

try:
    from pymongo import MongoClient, UpdateOne
except ImportError as e:
    raise ImportError("写 MongoDB 需要 pymongo，请先执行 pip install pymongo") from e

# ---- MongoDB 连接配置 ----
MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB = "JD_Product"
# 连不上时多久放弃（毫秒）。pymongo 默认要傻等 30 秒，这里改成快速失败。
MONGO_TIMEOUT_MS = 5000


def _keyword_slug(keyword: str) -> str:
    """把关键词转成能安全当文件名用的形式（去掉路径分隔符等非法字符）。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", keyword).strip("_") or "result"


def save_to_json(goods: list, path: str = "jd_goods.json") -> None:
    """把本次抓到的商品并入 JSON 文件（按 wareId 去重），不覆盖已有结果。

    :param goods: 商品列表，每项至少含 wareId 字段
    :param path:  目标文件；不存在则新建
    """
    out = Path(path)

    merged = []
    index = {}   # wareId -> 在 merged 里的下标
    if out.exists():
        try:
            for old in json.loads(out.read_text(encoding="utf-8")):
                key = old.get("wareId")
                if key is not None and key not in index:
                    index[key] = len(merged)
                    merged.append(old)
        except Exception as e:
            print(f"[!] 读取已有 {out.name} 失败，本次无法合并，将整文件重写: {e}")

    added = updated = 0
    for g in goods:
        key = g.get("wareId")
        if key is not None and key in index:
            merged[index[key]] = g       # 用本次的数据更新旧条目
            updated += 1
        else:
            if key is not None:
                index[key] = len(merged)
            merged.append(g)
            added += 1

    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"[+] 已写入 {out.resolve()}：本次新增 {added} 条、更新 {updated} 条，"
          f"文件累计 {len(merged)} 条")


def _mongo_safe_name(keyword: str) -> str:
    name = re.sub(r"[$\x00]+", "_", keyword).strip()
    if not name:
        return "result"
    if name.startswith("system."):
        return "_" + name
    return name


def save_to_mongo(goods: list, keyword: str,
                  uri: str = MONGO_URI, db_name: str = MONGO_DB) -> None:
    """
    :param goods:   商品列表，每项至少含 wareId 字段
    :param keyword: 搜索关键词，直接作为集合名
    :param uri:     MongoDB 连接串
    :param db_name: 库名，默认 JD_Product
    """ 
    if not goods:
        print("[!] 没有数据，跳过写库")
        return

    coll_name = _mongo_safe_name(keyword)

    with MongoClient(uri, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS) as client:
        coll = client[db_name][coll_name]
        # wareId 建唯一索引
        coll.create_index("wareId", unique=True)

        result = coll.bulk_write(
            [UpdateOne({"wareId": g.get("wareId")}, {"$set": g}, upsert=True)
             for g in goods],
            ordered=False,
        )
        total = coll.estimated_document_count()

    print(f"[+] 已写入 MongoDB {db_name}.{coll_name}："
          f"本次新增 {result.upserted_count} 条、命中已有 {result.matched_count} 条，"
          f"集合累计 {total} 条")


