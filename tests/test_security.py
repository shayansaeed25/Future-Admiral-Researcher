import pathlib
import re
import unittest

SECRET_PATTERNS = [
    ("Discord Webhook", re.compile(r"https://discord\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+")),
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("Groq API Key", re.compile(r"gsk_[0-9A-Za-z]{20,}")),
    ("Private Key Block", re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----")),
]

class SecurityScanTest(unittest.TestCase):
    def test_no_hardcoded_secrets_in_repo(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        leaks = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {".git", "__pycache__", ".venv", "venv"} for part in path.parts):
                continue
            if path.suffix.lower() not in {".py", ".yml", ".yaml", ".json", ".md", ".txt", ".sh", ".env"}:
                continue
            if path.name == "test_security.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for label, pat in SECRET_PATTERNS:
                if pat.search(text):
                    leaks.append(f"{label} found in {path.relative_to(root)}")
        self.assertEqual(leaks, [], f"Hardcoded secrets detected: {leaks}")

if __name__ == "__main__":
    unittest.main()
