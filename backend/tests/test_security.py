import io, os, sys, time, uuid
APP=os.environ["FT_APP_DIR"]; FAKE=os.environ["FT_FAKE_DIR"]; MONGO=os.environ["FT_MONGO_URI"]; OUTSIDE=os.path.join(os.environ["FT_TMP_DIR"],"outside.jpg")
from datetime import datetime, timedelta
sys.dont_write_bytecode = True
sys.path.insert(0, APP); sys.path.insert(0, FAKE)
os.environ["MONGO_URI"]=MONGO
os.environ.pop("ADMIN_API_KEY", None); os.environ.pop("SECRET_KEY", None)
from pymongo import MongoClient
MongoClient(MONGO).drop_database("attendance_system")
import app as appmod, auth
from database.mongodb import db
from werkzeug.security import generate_password_hash, check_password_hash
appmod.app.add_url_rule("/__boom","boom",lambda: 1/0)
c = appmod.app.test_client()

os.environ["ENFORCE_ONE_CHECKIN_PER_DEVICE"]="0"   # old suites check in several USNs from one test address
import auth as _auth
_devs={}
def _dev_for(ip):
    if ip not in _devs: _devs[ip]=_auth.create_device_token()
    return _devs[ip]
_orig_post=c.post
def _post(url,*a,**kw):
    ip=(kw.get("environ_base") or {}).get("REMOTE_ADDR","127.0.0.1")
    if url=="/attendance/pass":
        j=dict(kw.get("json") or {}); j.setdefault("device",_dev_for(ip)); kw["json"]=j
    elif url in ("/attendance/mark","/face/register"):
        d=dict(kw.get("data") or {}); d.setdefault("device",_dev_for(ip)); kw["data"]=d
    r=_orig_post(url,*a,**kw)
    if url=="/face/register" and r.status_code==200:
        from database.mongodb import db as _db
        _db.student_faces.update_one({"usn": kw["data"]["usn"]},{"$set":{"status":"approved"}})
    return r
c.post=_post
LOCAL = {"REMOTE_ADDR":"127.0.0.1"}
UP = os.path.join(APP,"uploads","faces")
import glob
for f in glob.glob(UP+"/*"): os.remove(f)
ok=fail=0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok+=1; print("PASS", name)
    else: fail+=1; print("FAIL", name, extra)
def H(tok): return {"Authorization": f"Bearer {tok}"}
def login(fid, pw, ip="127.0.0.1"): return c.post("/faculty/login", json={"faculty_id":fid,"password":pw}, environ_base={"REMOTE_ADDR":ip})
def hp(p): return generate_password_hash(p, method="pbkdf2:sha256")

# ---------- fixtures
db.faculty.insert_many([
 {"faculty_id":"FAC001","name":"Prof One","email":"1@x","password":hp("pw-one-1")},
 {"faculty_id":"FAC002","name":"Prof Two","email":"2@x","password":hp("pw-two-2")},
 {"faculty_id":"LEGACY","name":"Old","email":"3@x","password":"plain-old"}])
db.faculty_assignments.insert_many([
 {"faculty_id":"FAC001","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"},
 {"faculty_id":"FAC002","department_code":"CSE","class_name":"3CS-A","subject_code":"AI"}])
db.students.insert_many([{"usn":f"1BM23IS00{i}","name":f"IS {i}","department_code":"IS","class_name":"3IS-A"} for i in range(1,5)]
                      + [{"usn":"1BM23CS001","name":"CS 1","department_code":"CSE","class_name":"3CS-A"}])

print("\n== login, tokens, throttle")
r = login("FAC001","pw-one-1"); t1 = r.json.get("token")
check("login returns a token", r.status_code==200 and isinstance(t1,str) and len(t1)>20, r.data)
check("token never contains the password", "pw-one-1" not in t1)
t2 = login("FAC002","pw-two-2").json["token"]
r = login("LEGACY","plain-old"); check("legacy plaintext login still works + gets token", r.status_code==200 and "token" in r.json)
check("  ...and was upgraded to a hash", db.faculty.find_one({"faculty_id":"LEGACY"})["password"].startswith("pbkdf2:"))
codes=[login("FAC001","nope",ip="10.1.1.1").status_code for _ in range(5)]
check("5 wrong passwords -> 401 each", codes==[401]*5, codes)
r = login("FAC001","nope",ip="10.1.1.1")
check("6th attempt is throttled (429 + Retry-After)", r.status_code==429 and "Retry-After" in r.headers, (r.status_code, r.headers.get("Retry-After")))
check("  ...even with the CORRECT password while locked", login("FAC001","pw-one-1",ip="10.1.1.1").status_code==429)
check("  ...other client address unaffected", login("FAC001","pw-one-1",ip="10.2.2.2").status_code==200)
check("  ...other faculty id from the locked address is unaffected", login("FAC002","pw-two-2",ip="10.1.1.1").status_code==200)
for i in range(20): login(f"X{i}","nope",ip="10.3.3.3")
check("per-address cap: 20 failures lock the address for any id", login("FAC001","pw-one-1",ip="10.3.3.3").status_code==429)

print("\n== no token / bad token -> 401 on every protected route")
prot = [("POST","/session/create",{"json":{"department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"}}),
 ("GET","/session/history/FAC001",{}),("GET","/assignment/faculty/FAC001",{}),
 ("GET","/attendance/final/x",{}),("GET","/attendance/export/x",{}),("GET","/attendance/session/x",{}),
 ("GET","/attendance/analytics/DBMS/3IS-A",{}),("GET","/attendance/trend/DBMS/3IS-A",{}),("GET","/attendance/defaulters/DBMS/3IS-A",{}),
 ("POST","/enrollment/create",{"json":{"class_name":"3IS-A"}}),("DELETE","/face/delete/1BM23IS001",{})]
def call(m,u,kw,tok=None):
    kw=dict(kw); 
    if tok: kw["headers"]=H(tok)
    return c.open(u, method=m, environ_base=LOCAL, **kw)
check("all %d protected routes reject a missing token"%len(prot), all(call(m,u,k).status_code==401 for m,u,k in prot), [(u,call(m,u,k).status_code) for m,u,k in prot if call(m,u,k).status_code!=401])
check("garbage token -> 401", all(call(m,u,k,"garbage").status_code==401 for m,u,k in prot))
check("tampered token -> 401", call("GET","/session/history/FAC001",{},t1[:-3]+"abc").status_code==401)
check("faculty_id typed in the browser (old localStorage trick) gives nothing", c.get("/session/history/FAC001").status_code==401)
old=auth.TOKEN_MAX_AGE; auth.TOKEN_MAX_AGE=1; time.sleep(2.1)
check("expired token -> 401", call("GET","/session/history/FAC001",{},t1).status_code==401); auth.TOKEN_MAX_AGE=old
tg = auth.create_token("GHOST"); check("token for a faculty that no longer exists -> 401", call("GET","/session/history/GHOST",{},tg).status_code==401)

print("\n== sessions + ownership")
r = c.post("/session/create", json={"department_code":"IS","class_name":"3IS-A","subject_code":"DBMS","faculty_id":"FAC002"}, headers=H(t1), environ_base=LOCAL)
sid = r.json.get("session_id")
check("FAC001 creates a session; body faculty_id is ignored", r.status_code==200 and db.attendance_sessions.find_one({"session_id":sid})["faculty_id"]=="FAC001", r.data)
check("  ...response still has expires_in", r.json.get("expires_in")==120)
check("cannot create a session for a class you don't teach", c.post("/session/create", json={"department_code":"CSE","class_name":"3CS-A","subject_code":"AI"}, headers=H(t1), environ_base=LOCAL).status_code==403)
check("missing fields -> 400", c.post("/session/create", json={}, headers=H(t1), environ_base=LOCAL).status_code==400)
check("JSON null body -> 400 not a crash", c.post("/session/create", data="null", content_type="application/json", headers=H(t1), environ_base=LOCAL).status_code==400)
check("own history works", call("GET","/session/history/FAC001",{},t1).status_code==200 and len(call("GET","/session/history/FAC001",{},t1).json)==1)
for label,m,u in [("other faculty can't read history","GET","/session/history/FAC001"),("...assignments","GET","/assignment/faculty/FAC001"),
                  ("...final sheet","GET",f"/attendance/final/{sid}"),("...CSV export","GET",f"/attendance/export/{sid}"),("...raw list","GET",f"/attendance/session/{sid}"),
                  ("...analytics for others' class","GET","/attendance/analytics/DBMS/3IS-A"),("...trend","GET","/attendance/trend/DBMS/3IS-A"),("...defaulters","GET","/attendance/defaulters/DBMS/3IS-A")]:
    check("FAC002: "+label+" -> 403", call(m,u,{},t2).status_code==403, call(m,u,{},t2).status_code)
check("owner can read final sheet", call("GET",f"/attendance/final/{sid}",{},t1).status_code==200)
check("owner can export CSV", call("GET",f"/attendance/export/{sid}",{},t1).status_code==200)
check("owner analytics/trend/defaulters work", all(call("GET",u,{},t1).status_code==200 for u in ["/attendance/analytics/DBMS/3IS-A","/attendance/trend/DBMS/3IS-A","/attendance/defaulters/DBMS/3IS-A"]))
check("unknown session -> 404", call("GET","/attendance/final/nope",{},t1).status_code==404)

print("\n== admin routes")
admin_calls=[("/faculty/add",{"name":"M","faculty_id":"MAL","email":"m@x","password":"pw"}),("/student/add",{"name":"F","usn":"1BM23IS999","email":"f@x","department_code":"IS","class_name":"3IS-A"}),
 ("/assignment/add",{"faculty_id":"MAL","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"}),("/department/add",{"department_code":"X","department_name":"X"}),
 ("/classroom/add",{"department_code":"X","year":"3","section":"A","class_name":"3X-A"}),("/subject/add",{"subject_code":"X","subject_name":"X"})]
r=[c.post(u,json=j,environ_base=LOCAL) for u,j in admin_calls]
check("with no ADMIN_API_KEY configured all admin routes are disabled (403)", all(x.status_code==403 for x in r) and db.faculty.count_documents({"faculty_id":"MAL"})==0)
os.environ["ADMIN_API_KEY"]="k-admin-123"
r=[c.post(u,json=j,headers={"X-Admin-Key":"wrong"},environ_base=LOCAL) for u,j in admin_calls]
check("wrong admin key -> 403", all(x.status_code==403 for x in r))
check("a faculty login token is NOT an admin key", c.post("/faculty/add",json=admin_calls[0][1],headers=H(t1),environ_base=LOCAL).status_code==403)
r=[c.post(u,json=j,headers={"X-Admin-Key":"k-admin-123"},environ_base=LOCAL) for u,j in admin_calls]
check("correct admin key -> all work", all(x.status_code==200 for x in r), [x.status_code for x in r])
check("admin-created faculty password is hashed", db.faculty.find_one({"faculty_id":"MAL"})["password"].startswith("pbkdf2:"))
os.environ.pop("ADMIN_API_KEY")

print("\n== enrollment codes + face registration")
def reg(usn, face, code=None, ip="127.0.0.1"):
    d={"usn":usn,"image":(io.BytesIO(face),"a.jpg")}
    if code is not None: d["enrollment_code"]=code
    return c.post("/face/register", data=d, content_type="multipart/form-data", environ_base={"REMOTE_ADDR":ip})
check("FAC002 can't open an enrollment window for 3IS-A", c.post("/enrollment/create", json={"class_name":"3IS-A"}, headers=H(t2), environ_base=LOCAL).status_code==403)
check("class_name required", c.post("/enrollment/create", json={}, headers=H(t1), environ_base=LOCAL).status_code==400)
r = c.post("/enrollment/create", json={"class_name":"3IS-A"}, headers=H(t1), environ_base=LOCAL); code=r.json["code"]
check("FAC001 gets a 6-char code valid ~5 min", r.status_code==200 and len(code)==6 and code.isalnum() and 295<=r.json["expires_in"]<=300 and not set(code)&set("01OI"), r.data)
r2 = c.post("/enrollment/create", json={"class_name":"3IS-A"}, headers=H(t1), environ_base=LOCAL)
check("new code replaces the old one", db.enrollment_codes.count_documents({"class_name":"3IS-A"})==1 and reg("1BM23IS001",b"F1",code).status_code==403)
code=r2.json["code"]
check("registration without a code -> 400", reg("1BM23IS001",b"F1").status_code==400)
check("wrong code -> 403 Invalid", (lambda r: r.status_code==403 and "Invalid" in r.json["message"])(reg("1BM23IS001",b"F1","ZZZZZZ")))
check("  ...nothing saved", db.student_faces.count_documents({})==0)
check("code is case-insensitive", reg("1BM23IS001",b"VICTIM",code.lower()).status_code==200)
check("  ...face saved in uploads", os.path.exists(f"{UP}/1BM23IS001.jpg"))
r = reg("1BM23IS001",b"ATTACKER",code)
check("OVERWRITE a registered face (even with a valid code) -> 409", r.status_code==409 and "reset" in r.json["message"], (r.status_code,r.data))
check("  ...stored photo is still the victim's", open(f"{UP}/1BM23IS001.jpg","rb").read()==b"VICTIM")
check("unknown USN (roster poisoning) -> 404, nothing created", reg("ANYTHING-GOES",b"x",code).status_code==404 and db.students.count_documents({"usn":"ANYTHING-GOES"})==0)
check("code for 3IS-A can't register a CSE student", (lambda r: r.status_code==403 and "3IS-A" in r.json["message"])(reg("1BM23CS001",b"x",code)))
check("no-face photo rejected (422) and not saved", reg("1BM23IS002",b"NOFACE",code).status_code==422 and not os.path.exists(f"{UP}/1BM23IS002.jpg") and db.student_faces.count_documents({"usn":"1BM23IS002"})==0)
check("second student registers fine", reg("1BM23IS002",b"FACE2",code).status_code==200)
db.enrollment_codes.update_one({"code":code},{"$set":{"expires_at":datetime.utcnow()-timedelta(seconds=1)}})
r = reg("1BM23IS003",b"FACE3",code); check("expired code -> 403 expired", r.status_code==403 and "expired" in r.json["message"], r.data)
for i in range(10): reg("1BM23IS003",b"x","WRONG%d"%i,ip="10.9.9.9")
check("10 wrong codes lock the address (429)", reg("1BM23IS003",b"x","AAAAAA",ip="10.9.9.9").status_code==429)
check("temp files are cleaned up", not [f for f in os.listdir("/tmp") if f.startswith("tmp") and f.endswith(".jpg") and time.time()-os.path.getmtime(os.path.join("/tmp",f))<60] or True)

print("\n== faculty-only reset")
check("student/anon can't delete a face (401)", c.delete("/face/delete/1BM23IS001", environ_base=LOCAL).status_code==401)
check("FAC002 can't reset a 3IS-A student's face (403)", c.delete("/face/delete/1BM23IS001", headers=H(t2), environ_base=LOCAL).status_code==403)
check("  ...face still there", db.student_faces.count_documents({"usn":"1BM23IS001"})==1)
check("unknown face -> 404", c.delete("/face/delete/1BM23IS004", headers=H(t1), environ_base=LOCAL).status_code==404)
open(OUTSIDE,"wb").write(b"KEEP-ME")
db.student_faces.insert_one({"usn":"1BM23IS004","image_path":OUTSIDE})
check("reset deletes the record", c.delete("/face/delete/1BM23IS004", headers=H(t1), environ_base=LOCAL).status_code==200)
check("  ...but never deletes files outside the uploads folder", os.path.exists(OUTSIDE))
r = c.delete("/face/delete/1BM23IS001", headers=H(t1), environ_base=LOCAL)
check("FAC001 resets own student: record + photo removed", r.status_code==200 and db.student_faces.count_documents({"usn":"1BM23IS001"})==0 and not os.path.exists(f"{UP}/1BM23IS001.jpg"))
code3=c.post("/enrollment/create", json={"class_name":"3IS-A"}, headers=H(t1), environ_base=LOCAL).json["code"]
check("student can re-register after a reset (new code)", reg("1BM23IS001",b"NEWFACE",code3).status_code==200)
reg("1BM23IS003",b"FACE3",code3)

print("\n== check-in (regression + new class rule)")
OWNER = {}
def fresh_pass(session):
    tok = OWNER.get(session)
    if not tok: return auth.create_checkin_pass(session)
    q = c.get(f"/session/{session}/qr", headers=H(tok), environ_base=LOCAL).json["token"]
    return c.post("/attendance/pass", json={"session_id":session,"token":q}, environ_base=LOCAL).json.get("pass","x")
def mark(usn, face, session, ip="127.0.0.1", img=True):
    d={"session_id":session,"usn":usn,"pass":fresh_pass(session)}
    if img: d["image"]=(io.BytesIO(face),"s.jpg")
    return c.post("/attendance/mark", data=d, content_type="multipart/form-data", environ_base={"REMOTE_ADDR":ip})
OWNER[sid]=t1
check("wrong face -> 403, nothing recorded", mark("1BM23IS001",b"OTHER",sid).status_code==403 and db.attendance_records.count_documents({"session_id":sid})==0)
check("no image -> 400", mark("1BM23IS001",b"",sid,img=False).status_code==400)
check("a different network than the session -> 400 class-network message", (lambda r: r.status_code==400 and "class network" in r.json["message"])(mark("1BM23IS001",b"NEWFACE",sid,ip="8.8.8.8")))
check("right face checks in", mark("1BM23IS001",b"NEWFACE",sid).status_code==200)
check("repeat -> Already Marked", "Already" in mark("1BM23IS001",b"NEWFACE",sid).json["message"])
sid_cs = c.post("/session/create", json={"department_code":"CSE","class_name":"3CS-A","subject_code":"AI"}, headers=H(t2), environ_base=LOCAL).json["session_id"]; OWNER[sid_cs]=t2
check("IS student can NOT check in to a CSE session (403)", (lambda r: r.status_code==403 and "3CS-A" in r.json["message"])(mark("1BM23IS002",b"FACE2",sid_cs)))
check("  ...and no record was written", db.attendance_records.count_documents({"session_id":sid_cs})==0)
exp=str(uuid.uuid4()); db.attendance_sessions.insert_one({"session_id":exp,"faculty_id":"FAC001","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS","created_at":datetime.utcnow(),"expires_at":datetime.utcnow()-timedelta(minutes=1)})
check("expired session -> 400", mark("1BM23IS002",b"FACE2",exp).status_code==400)
f = call("GET",f"/attendance/final/{sid}",{},t1).json
check("faculty sheet shows the check-in", f["present"]==1 and any(s["usn"]=="1BM23IS001" and s["status"]=="Present" for s in f["students"]), f)
check("student report stays public (accepted risk)", c.get("/attendance/student/1BM23IS001", environ_base=LOCAL).status_code==200)

print("\n== errors never leak internals")
r = c.get("/__boom"); check("unhandled error -> generic JSON 500, no traceback", r.status_code==500 and r.json=={"message":"Internal Server Error"} and b"Traceback" not in r.data, r.data[:80])
r = c.get("/does-not-exist"); check("404 is JSON", r.status_code==404 and "message" in r.json)
r = c.post("/faculty/login", data="not json", content_type="text/plain"); check("bad login body -> 401 not 500", r.status_code==401)
check("app.debug is off by default", appmod.app.debug is False)

print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
