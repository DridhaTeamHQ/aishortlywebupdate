"""Patch orchestrator.py to call mark_published_in_supabase after successful publish."""
import pathlib, re

target = pathlib.Path(__file__).parent.parent / "core" / "orchestrator.py"
content = target.read_text(encoding="utf-8")

# Find the success block and inject the Supabase call after mark_story_success
old = (
    "                        if cluster_story_key:\n"
    "                            published_story_keys.add(cluster_story_key)\n"
    "                            self.memory.mark_story_success(\n"
    "                                cluster_story_key,\n"
    "                                article_url,\n"
    "                                getattr(cluster, \"canonical_title\", \"\"),\n"
    "                            )\n"
    "                        published_from_cluster = True\n"
    "                        break\n"
)
new = (
    "                        if cluster_story_key:\n"
    "                            published_story_keys.add(cluster_story_key)\n"
    "                            self.memory.mark_story_success(\n"
    "                                cluster_story_key,\n"
    "                                article_url,\n"
    "                                getattr(cluster, \"canonical_title\", \"\"),\n"
    "                            )\n"
    "                        # Persist to Supabase so future runs on any worker skip this article\n"
    "                        self.memory.mark_published_in_supabase(\n"
    "                            article_url,\n"
    "                            title=getattr(cluster, \"canonical_title\", \"\"),\n"
    "                            story_key=cluster_story_key or \"\",\n"
    "                        )\n"
    "                        published_from_cluster = True\n"
    "                        break\n"
)

# Normalise line endings before searching
normalised = content.replace("\r\n", "\n")
if old in normalised:
    patched = normalised.replace(old, new, 1)
    # Restore original line endings
    if "\r\n" in content:
        patched = patched.replace("\n", "\r\n")
    target.write_text(patched, encoding="utf-8")
    print("PATCHED OK")
else:
    # Try searching for a shorter anchor
    anchor = 'published_from_cluster = True\n                        break'
    idx = normalised.find(anchor)
    print(f"NOT FOUND - anchor idx={idx}")
    # Show surrounding context
    if idx >= 0:
        print(repr(normalised[max(0, idx-400):idx+100]))
