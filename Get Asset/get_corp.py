import requests
import base64
import json
import time

# ================= 配置 =================
CLIENT_ID = "acbc2d81feaf4be6a9d9fb93adef2ae4"
CLIENT_SECRET = "eat_AO7mAL5LeMDdObuwrJwF64b4t1h7kgQH_RxLbc"

ACCESS_TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6IkpXVC1TaWduYXR1cmUtS2V5IiwidHlwIjoiSldUIn0.eyJzY3AiOlsiZXNpLWFzc2V0cy5yZWFkX2Fzc2V0cy52MSIsImVzaS1hc3NldHMucmVhZF9jb3Jwb3JhdGlvbl9hc3NldHMudjEiLCJlc2ktaW5kdXN0cnkucmVhZF9jaGFyYWN0ZXJfam9icy52MSIsImVzaS1pbmR1c3RyeS5yZWFkX2NvcnBvcmF0aW9uX2pvYnMudjEiLCJlc2ktY2hhcmFjdGVycy5yZWFkX2JsdWVwcmludHMudjEiLCJlc2ktY29ycG9yYXRpb25zLnJlYWRfYmx1ZXByaW50cy52MSIsImVzaS11bml2ZXJzZS5yZWFkX3N0cnVjdHVyZXMudjEiXSwianRpIjoiNDk4Nzk0NTItNmVjMi00YWUzLTk3YjItZjliZjgyNDM4ZjhlIiwia2lkIjoiSldULVNpZ25hdHVyZS1LZXkiLCJzdWIiOiJDSEFSQUNURVI6RVZFOjIxMjMwNjgxMDAiLCJhenAiOiJhY2JjMmQ4MWZlYWY0YmU2YTlkOWZiOTNhZGVmMmFlNCIsInRlbmFudCI6InRyYW5xdWlsaXR5IiwidGllciI6ImxpdmUiLCJyZWdpb24iOiJ3b3JsZCIsImF1ZCI6WyJhY2JjMmQ4MWZlYWY0YmU2YTlkOWZiOTNhZGVmMmFlNCIsIkVWRSBPbmxpbmUiXSwibmFtZSI6IlZvdXJhcyBJa2thbGEiLCJvd25lciI6IklFRjNOZnA0UXZ2QlFVSExXNG9VWHlBOWhNWT0iLCJleHAiOjE3NzA2NjEyMTgsImlhdCI6MTc3MDY2MDAxOCwiaXNzIjoiaHR0cHM6Ly9sb2dpbi5ldmVvbmxpbmUuY29tIn0.NCULCB_OhA--o_tC_8MTkdiWZpT3IzHzMys_7U-pRsXhRxJW0oSbmdeHLV7iXgk_G1uu3F8O9S4xKleXiV6X7YDlZfhJrfXnoGD_UmVzJmEeV-vdKgZT4MmAQZvSXVmNaCTt_QeBWQ_YDCNkvKIvrbr7jIUNx3BoZrdqMCwK7ptA7Rp7E7mj0lIfPUoR3Ol0zmCdkzLUYEeomeS8J0Wyx_R0-LwKyKuhWWuXmejGsa59Ew938SJGmb9j5NUBc29PkjuBL_reKvkC0bROBrtsPZZQ5kP9f9N-6QtFr_7KKow24RNq3i1oponDNffC-QpsoE3EJmwzqa-s7IboMCRVNA"
REFRESH_TOKEN = "KAVnjSJITE657G9Gcknyhw=="
CORP_ID = 98822194

LOGIN_BASE = "https://login.eveonline.com"
ESI_BASE = "https://esi.evetech.net/latest"
USER_AGENT = "CorpAssetIndustryScript/1.0"
# =======================================


def refresh_access_token(refresh_token):
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }

    r = requests.post(f"{LOGIN_BASE}/v2/oauth/token", headers=headers, data=data, timeout=15)
    if r.status_code != 200:
        print("Refresh token failed:", r.status_code, r.text)
        raise RuntimeError("Refresh token failed")
    token = r.json()
    return token["access_token"], token.get("refresh_token", refresh_token)


def get_all_pages(url, access_token):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": USER_AGENT
    }
    page = 1
    items = []

    while True:
        r = requests.get(url, headers=headers, params={"page": page}, timeout=20)
        if r.status_code == 403:
            raise PermissionError(f"403 Forbidden: 检查角色权限或 scope ({url})")
        r.raise_for_status()
        items.extend(r.json())

        pages = int(r.headers.get("X-Pages", 1))
        if page >= pages:
            break
        page += 1
        time.sleep(0.2)

    return items


def main():
    # 刷新 token
    access_token, new_refresh_token = refresh_access_token(REFRESH_TOKEN)
    print(f"使用 corp_id: {CORP_ID}")

    # 1️⃣ 获取军团资产
    assets_url = f"{ESI_BASE}/corporations/{CORP_ID}/assets/"
    assets = get_all_pages(assets_url, access_token)
    print(f"获取到军团资产 {len(assets)} 条")
    with open("corp_assets.json", "w", encoding="utf-8") as f:
        json.dump(assets, f, ensure_ascii=False, indent=2)

    # 2️⃣ 获取军团工业 jobs
    jobs_url = f"{ESI_BASE}/corporations/{CORP_ID}/industry/jobs/"
    jobs = get_all_pages(jobs_url, access_token)
    print(f"获取到军团工业 jobs {len(jobs)} 条")
    with open("corp_industry.json", "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

    # 3️⃣ 获取军团蓝图
    blueprints_url = f"{ESI_BASE}/corporations/{CORP_ID}/blueprints/"
    blueprints = get_all_pages(blueprints_url, access_token)
    print(f"获取到军团蓝图 {len(blueprints)} 条")
    with open("corp_blueprints.json", "w", encoding="utf-8") as f:
        json.dump(blueprints, f, ensure_ascii=False, indent=2)

    if new_refresh_token != REFRESH_TOKEN:
        print("⚠️ refresh_token 已更新，请保存新值")


if __name__ == "__main__":
    main()