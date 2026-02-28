import urllib.parse

scopes = [
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
    "esi-industry.read_character_jobs.v1",
    "esi-industry.read_corporation_jobs.v1",
    "esi-industry.read_character_blueprints.v1",
    "esi-industry.read_corporation_blueprints.v1",
    "esi-universe.read_structures.v1"
]
scope_param = urllib.parse.quote(" ".join(scopes))
print(scope_param)
# 输出: esi-assets.read_assets.v1%20esi-assets.read_corporation_assets.v1%20esi-industry.read_character_jobs.v1
