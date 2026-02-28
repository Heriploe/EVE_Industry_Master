import requests
import base64
import time

# ================== 配置区 ==================
CLIENT_ID = "acbc2d81feaf4be6a9d9fb93adef2ae4"
CLIENT_SECRET = "eat_AO7mAL5LeMDdObuwrJwF64b4t1h7kgQH_RxLbc"

ACCESS_TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6IkpXVC1TaWduYXR1cmUtS2V5IiwidHlwIjoiSldUIn0.eyJzY3AiOiJlc2ktYXNzZXRzLnJlYWRfYXNzZXRzLnYxIiwianRpIjoiMTgyMDMzNTMtZDYyMC00OGY0LTliZDMtMDMxMGJmYTZhMGIwIiwia2lkIjoiSldULVNpZ25hdHVyZS1LZXkiLCJzdWIiOiJDSEFSQUNURVI6RVZFOjIxMTkxNDkyODAiLCJhenAiOiJhY2JjMmQ4MWZlYWY0YmU2YTlkOWZiOTNhZGVmMmFlNCIsInRlbmFudCI6InRyYW5xdWlsaXR5IiwidGllciI6ImxpdmUiLCJyZWdpb24iOiJ3b3JsZCIsImF1ZCI6WyJhY2JjMmQ4MWZlYWY0YmU2YTlkOWZiOTNhZGVmMmFlNCIsIkVWRSBPbmxpbmUiXSwibmFtZSI6IlRlbnNlbyIsIm93bmVyIjoiRGdYZTdhcExIZ1dXUElhVHlGVkR2RUNCVk5BPSIsImV4cCI6MTc3MDUwNTQwOCwiaWF0IjoxNzcwNTA0MjA4LCJpc3MiOiJodHRwczovL2xvZ2luLmV2ZW9ubGluZS5jb20ifQ.ea6XrM16MIHthS7hpBIbTaAJRYO-QD8h2Z3mp2o1Iy5J4G-UOB8oNdbYUtqOjbWHbMBsg7zy6RLfA6Dmc6eQZRz0ijCp-qnQNWUT_SHC3kAI4DKVKKvooVg0weN78QEjENgVd-fwB8BVRcuM_TD20D7Sfs10J3buVaieErtUxLYm2n7NW2nR-XpAXfYgOKobdlM0yNZ_ZxY2A84V1sqlWxGD-bv5qvNYBH5Ea06ggfv8u_ZC47zlKzpeFeCqGn8oq_otlsKoz-2GZ26EdxVuobboifxjzXFKmebKpRh9rhH8aqgAEXdtk2HX9JsVs5UkHvk1LtfGpEMa6YpIRF7e3w"
REFRESH_TOKEN = "yHGNjYQQNUOMMO9Q5nrZ4g=="

ESI_BASE = "https://esi.evetech.net/latest"
LOGIN_BASE = "https://login.eveonline.com"
# ============================================


def refresh_access_token(refresh_token):
    auth = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")
    ).decode("utf-8")

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "AssetScript/1.0"
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }

    r = requests.post(
        "https://login.eveonline.com/v2/oauth/token",
        headers=headers,
        data=data,
        timeout=15
    )

    # 👇 关键调试信息
    if r.status_code != 200:
        print("Status:", r.status_code)
        print("Response:", r.text)
        raise RuntimeError("Refresh token failed")

    token = r.json()
    return token["access_token"], token.get("refresh_token", refresh_token)


def get_character_id(access_token):
    """
    从 access_token 中获取角色 ID
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "AssetScript/1.0"
    }

    r = requests.get(
        f"{LOGIN_BASE}/oauth/verify",
        headers=headers,
        timeout=10
    )
    r.raise_for_status()

    data = r.json()
    return data["CharacterID"], data["CharacterName"]


def get_all_character_assets(character_id, access_token):
    """
    自动处理 X-Pages，获取全部资产
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "AssetScript/1.0"
    }

    page = 1
    assets = []

    while True:
        r = requests.get(
            f"{ESI_BASE}/characters/{character_id}/assets/",
            headers=headers,
            params={"page": page},
            timeout=20
        )
        r.raise_for_status()

        assets.extend(r.json())

        pages = int(r.headers.get("X-Pages", 1))
        if page >= pages:
            break

        page += 1
        time.sleep(0.2)  # 防止 420

    return assets


def main():
    # 1. 刷新 token
    access_token, new_refresh_token = refresh_access_token(REFRESH_TOKEN)

    # 2. 获取角色信息
    char_id, char_name = get_character_id(access_token)
    print(f"Character: {char_name} ({char_id})")

    # 3. 获取资产
    assets = get_all_character_assets(char_id, access_token)
    print(f"Total assets: {len(assets)}")

    # 4. 打印部分示例
    for a in assets[:10]:
        print(a)

    # 如果 refresh_token 发生变化，记得保存
    if new_refresh_token != REFRESH_TOKEN:
        print("⚠ refresh_token 已更新，请保存新值")


if __name__ == "__main__":
    main()
