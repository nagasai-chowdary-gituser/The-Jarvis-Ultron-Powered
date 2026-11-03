"""Recipe: count_words - the first earned skill, and the template for the rest.

A recipe is a module in tools/ that exports SCHEMA + run(). Dropping the file in
is the whole install (recipes.py auto-discovers it). This one is deliberately
simple and safe - it reads a workspace file and counts it - so it works as the
worked example a promoted recipe is modelled on.
"""
SCHEMA = {
    "name": "count_words",
    "description": "Count the words and lines in a text file in the workspace.",
    "phone_safe": True,          # reading a workspace file is safe from a phone
    "telegram_safe": True,
}


def run(path, **kwargs):
    """Count words and lines in a workspace file. Returns a dict, never raises."""
    from lib import files
    try:
        text = files.read(path)
    except Exception as e:
        return {"ok": False, "error": f"could not read {path!r}: {e}"}
    return {"ok": True, "path": path,
            "words": len(text.split()),
            "lines": text.count("\n") + 1 if text else 0,
            "chars": len(text)}
