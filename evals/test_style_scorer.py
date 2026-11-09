"""style 域判定与数据集的单元测试。不发任何请求。

    python -m unittest evals.test_style_scorer -v
"""

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
DATA_DIR = EVALS_DIR / "data"


def load_run_eval():
    path = EVALS_DIR / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_eval = load_run_eval()

# 两句话，结尾句含数字且够长，因此不会被判成金句收尾。
CLEAN = (
    "网关在入口按 IP 限流，路由前按模型限流，上游失败后按重试预算限流。"
    "超过预算时返回 429 并带上 Retry-After 响应头，缓存层只保存成功响应。"
)


def score(text, tags=None):
    return run_eval.score_style(text, {"tags": list(tags or [])})


class BannedLexiconTests(unittest.TestCase):
    def test_clean_prose_passes(self):
        result = score(CLEAN)
        self.assertEqual(result["banned_hits"], [])
        self.assertEqual(result["dash_hits"], 0)
        self.assertEqual(result["contrast_hits"], 0)
        self.assertTrue(result["style_pass"])

    def test_english_banned_word_fails(self):
        result = score(CLEAN + " Let's delve into the cache layer.")
        self.assertIn("delve", result["banned_hits"])
        self.assertFalse(result["style_pass"])

    def test_chinese_banned_word_fails(self):
        result = score(CLEAN + " 这套做法会赋能下游团队。")
        self.assertIn("赋能", result["banned_hits"])
        self.assertFalse(result["style_pass"])

    def test_banned_word_needs_word_boundary(self):
        # realm 命中，realmodel 不命中。
        self.assertEqual(score(CLEAN + " realm")["banned_hits"], ["realm"])
        self.assertEqual(score(CLEAN + " realmodel")["banned_hits"], [])


class ContrastFrameTests(unittest.TestCase):
    def test_chinese_contrast_frame_fails(self):
        result = score(CLEAN + " 这不是工具问题而是流程问题。")
        self.assertGreaterEqual(result["contrast_hits"], 1)
        self.assertFalse(result["style_pass"])

    def test_english_contrast_frame_fails(self):
        result = score(CLEAN + " It is not just a cache but a router.")
        self.assertGreaterEqual(result["contrast_hits"], 1)
        self.assertFalse(result["style_pass"])


class DashTests(unittest.TestCase):
    def test_em_dash_fails(self):
        result = score(CLEAN + " 这一步很关键——真的。")
        self.assertGreaterEqual(result["dash_hits"], 1)
        self.assertFalse(result["style_pass"])

    def test_dashes_inside_code_block_ignored(self):
        text = CLEAN + "\n\n```bash\nmake build --flag --out=x   # 注释 — 不动\n```\n"
        result = score(text)
        self.assertEqual(result["dash_hits"], 0)
        self.assertTrue(result["style_pass"])

    def test_table_separator_dashes_ignored(self):
        text = CLEAN + "\n\n| 参数 | 默认值 |\n| --- | --- |\n| timeout | 180 |\n"
        self.assertEqual(score(text)["dash_hits"], 0)


class ProseShapeTests(unittest.TestCase):
    def test_aphorism_tail_detected(self):
        result = score("第一段讲背景。第二段讲做法。工具只是放大器。")
        self.assertTrue(result["aphorism_tail"])

    def test_long_tail_is_not_aphorism(self):
        self.assertFalse(score(CLEAN)["aphorism_tail"])

    def test_hedge_stack_detected(self):
        result = score("这个方案可能有问题，或许要多测一轮再合并。")
        self.assertTrue(result["hedge_stack"])

    def test_single_hedge_is_not_a_stack(self):
        self.assertFalse(score("这个方案可能有边界问题，先合并再观察。")["hedge_stack"])

    def test_three_short_bullets_flagged_as_triple(self):
        text = "校验步骤：\n\n- 读文件\n- 校验字段\n- 校验哈希\n"
        self.assertTrue(score(text)["triple"])

    def test_three_long_bullets_are_not_triple(self):
        text = (
            "校验步骤：\n\n"
            "- 读取整个 JSONL 文件并记录行数，遇到非法行立即中止\n"
            "- 逐行校验必需字段是否齐全，缺失字段打印行号和字段名\n"
            "- 对每个文件重算 SHA-256 并与清单里的值比对，不一致就退出\n"
        )
        self.assertFalse(score(text)["triple"])


class MermaidTests(unittest.TestCase):
    def test_lowercase_end_as_id_flagged(self):
        text = '```mermaid\nflowchart TD\n  A["start"] --> end\n  end --> B["done"]\n```'
        payload = score(text, ["mermaid"])["mermaid"]
        self.assertTrue(payload["present"])
        self.assertGreaterEqual(payload["bad_ids"], 1)
        self.assertFalse(score(text, ["mermaid"])["style_pass"])

    def test_unquoted_label_with_brackets_flagged(self):
        text = '```mermaid\nflowchart TD\n  A[parse (v2)] --> B["ok"]\n```'
        payload = score(text, ["mermaid"])["mermaid"]
        self.assertEqual(payload["unquoted_labels"], 1)
        self.assertFalse(score(text, ["mermaid"])["style_pass"])

    def test_compliant_block_passes(self):
        text = (
            "```mermaid\nflowchart LR\n"
            '  A["client / app"] --> B["gateway"]\n'
            '  B --> C["upstream"]\n'
            "```\n\n网关只做鉴权和限流，路由表由上游注册中心下发。"
        )
        payload = score(text, ["mermaid"])
        self.assertTrue(payload["mermaid"]["present"])
        self.assertEqual(payload["mermaid"]["bad_ids"], 0)
        self.assertEqual(payload["mermaid"]["unquoted_labels"], 0)
        self.assertTrue(payload["style_pass"])

    def test_mermaid_tag_without_block_fails(self):
        payload = score(CLEAN, ["mermaid"])
        self.assertFalse(payload["mermaid"]["present"])
        self.assertFalse(payload["style_pass"])


class TableTests(unittest.TestCase):
    def test_table_rows_counted(self):
        text = (
            "| 参数 | 默认值 | 说明 |\n"
            "|---|---|---|\n"
            "| timeout | 180 | 秒 |\n"
            "| retries | 2 | 次数 |\n"
        )
        payload = score(text, ["table"])
        self.assertTrue(payload["table"]["present"])
        self.assertGreaterEqual(payload["table"]["rows"], 3)
        self.assertTrue(payload["style_pass"])

    def test_table_tag_with_two_rows_fails(self):
        text = "| 参数 | 默认值 |\n|---|---|\n"
        payload = score(text, ["table"])
        self.assertLess(payload["table"]["rows"], 3)
        self.assertFalse(payload["style_pass"])


class StyleDataTests(unittest.TestCase):
    def load_samples(self):
        path = DATA_DIR / "full-v3" / "style.jsonl"
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_dataset_shape_and_manifest_hash(self):
        path = DATA_DIR / "full-v3" / "style.jsonl"
        samples = self.load_samples()
        self.assertEqual(len(samples), 20)
        self.assertTrue(all(item["domain"] == "style" for item in samples))
        self.assertTrue(all(item["source"] == "handwritten" for item in samples))
        self.assertTrue(all(item["expect"] == "comply" for item in samples))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = json.loads((DATA_DIR / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["files"]["full-v3/style.jsonl"], digest)

    def test_dataset_tag_split(self):
        samples = self.load_samples()
        mermaid = [item for item in samples if "mermaid" in item["tags"]]
        table = [item for item in samples if "table" in item["tags"]]
        both = [
            item
            for item in samples
            if "mermaid" in item["tags"] and "table" in item["tags"]
        ]
        prose = [
            item
            for item in samples
            if "mermaid" not in item["tags"] and "table" not in item["tags"]
        ]
        self.assertEqual(len(prose), 8)
        self.assertEqual(len(mermaid) - len(both), 6)
        self.assertEqual(len(table) - len(both), 4)
        self.assertEqual(len(both), 2)


if __name__ == "__main__":
    unittest.main()
