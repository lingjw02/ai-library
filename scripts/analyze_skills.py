import json
from collections import defaultdict
from pathlib import Path

skills = json.load(open('scratch_skills.json', 'r', encoding='utf-8'))
print(f"Total discovered skills: {len(skills)}")

# Let's inspect directories, names, and keywords
by_source = defaultdict(list)
for s in skills:
    for loc in s.get('locations', []):
        by_source[loc].append(s['title'])

print("\n--- By Source ---")
for src, items in by_source.items():
    print(f"{src}: {len(items)} skills")

print("\n--- Sample Skill Titles ---")
titles = [s['title'] for s in skills]
for t in sorted(titles):
    print(t)
