import requests
import base64

client_id = "acbc2d81feaf4be6a9d9fb93adef2ae4"
client_secret = "eat_AO7mAL5LeMDdObuwrJwF64b4t1h7kgQH_RxLbc"
code = "zW7EGp-ockGxg_QrUCLeCQ"
redirect_uri = "http://localhost/callback"

auth = base64.b64encode(
    f"{client_id}:{client_secret}".encode()
).decode()

headers = {
    "Authorization": f"Basic {auth}",
    "Content-Type": "application/x-www-form-urlencoded"
}

data = {
    "grant_type": "authorization_code",
    "code": code,
    "redirect_uri": redirect_uri
}

r = requests.post(
    "https://login.eveonline.com/v2/oauth/token",
    headers=headers,
    data=data
)

token = r.json()
print(token)
