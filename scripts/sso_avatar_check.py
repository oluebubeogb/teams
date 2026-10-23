# Manual check: Teams (AUTH_MODE=accounts, :8009) against a local Collab Accounts (:1997).
import httpx, io
from PIL import Image
A="http://localhost:1997"; T="http://localhost:8009"
a=httpx.Client(base_url=A); a.post("/auth/login",json={"login":"ada","password":"Passw0rd!x"})
tok=a.cookies["access_token"]
t=httpx.Client(base_url=T, cookies={"access_token":tok})
me=t.get("/api/me").json(); print("teams me:", me["user"] and me["user"]["username"], list(me))
r=httpx.get(T+"/api/me"); print("anon:", r.status_code, r.text[:120])
r=httpx.get(T+"/library",follow_redirects=False); print("anon /library ->", r.status_code, r.headers.get("location"))
b=io.BytesIO(); Image.new("RGB",(500,500),(200,80,60)).save(b,"PNG")
r=t.post("/api/me/avatar",files={"file":("p.png",b.getvalue(),"image/png")}); print("teams->accounts avatar:", r.status_code, r.json().get("avatar"))
print("accounts me avatar:", a.get("/auth/me").json()["avatar_url"])
print("teams me avatar:", t.get("/api/me").json()["user"].get("avatar"))
r=t.delete("/api/me/avatar"); print("delete:", r.status_code, a.get("/auth/me").json()["avatar_url"])
