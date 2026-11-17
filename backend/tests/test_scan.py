import io, os, sys, glob
APP=os.environ["FT_APP_DIR"]; FAKE=os.environ["FT_FAKE_DIR"]; MONGO=os.environ["FT_MONGO_URI"]; OUTSIDE=os.path.join(os.environ["FT_TMP_DIR"],"outside.jpg")
sys.dont_write_bytecode = True
sys.path.insert(0, APP); sys.path.insert(0, FAKE)
os.environ["MONGO_URI"]=MONGO
for k in ("FACE_MATCH_THRESHOLD","FACE_MODEL","ADMIN_API_KEY","SECRET_KEY","REQUIRE_LIVE_CAPTURE","ANTI_SPOOFING"): os.environ.pop(k,None)
from pymongo import MongoClient
MongoClient(MONGO).drop_database("attendance_system")
for f in glob.glob(APP+"/uploads/faces/*"): os.remove(f)
import app as appmod, auth
from database.mongodb import db
from werkzeug.security import generate_password_hash
c=appmod.app.test_client(); L={"REMOTE_ADDR":"127.0.0.1"}; H=lambda t:{"Authorization":f"Bearer {t}"}

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
ok=fail=0
def check(n,cond,extra=""):
    global ok,fail
    if cond: ok+=1; print("PASS",n)
    else: fail+=1; print("FAIL",n,extra)
db.faculty.insert_one({"faculty_id":"F1","name":"P","email":"a","password":generate_password_hash("pw",method="pbkdf2:sha256")})
db.faculty_assignments.insert_one({"faculty_id":"F1","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"})
db.students.insert_many([{"usn":"1BM23IS001","name":"Asha Rao","department_code":"IS","class_name":"3IS-A"},{"usn":"1BM23IS002","name":"Ravi K","department_code":"IS","class_name":"3IS-A"}])
t=c.post("/faculty/login",json={"faculty_id":"F1","password":"pw"}).json["token"]
code=c.post("/enrollment/create",json={"class_name":"3IS-A"},headers=H(t),environ_base=L).json["code"]
for u in ("1BM23IS001","1BM23IS002"):
    c.post("/face/register",data={"usn":u,"enrollment_code":code,"image":(io.BytesIO(b"REG-"+u.encode()),"a.jpg")},content_type="multipart/form-data",environ_base=L)
sid=c.post("/session/create",json={"department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"},headers=H(t),environ_base=L).json["session_id"]
def getpass():
    tok=c.get(f"/session/{sid}/qr",headers=H(t),environ_base=L).json["token"]
    return c.post("/attendance/pass",json={"session_id":sid,"token":tok},environ_base=L).json["pass"]
P=getpass()
def mark(usn,img,ip="127.0.0.1",pas=None):
    return c.post("/attendance/mark",data={"session_id":sid,"usn":usn,"pass":pas or P,"capture_mode":"live","image":(io.BytesIO(img),"s.jpg")},content_type="multipart/form-data",environ_base={"REMOTE_ADDR":ip})
print("== no face is not a mismatch")
for i in range(12): r=mark("1BM23IS001",b"NOFACE")
check("12 'no face' frames in a row -> still 422, never locked", r.status_code==422)
print("== repeated wrong faces are limited")
codes=[mark("1BM23IS001",b"FAR-stranger").status_code for _ in range(8)]
check("8 wrong-face frames -> 403 each", codes==[403]*8, codes)
r=mark("1BM23IS001",b"FAR-stranger"); check("9th is blocked (429 + Retry-After)", r.status_code==429 and "Too many" in r.json["message"], r.data)
check("  ...even the CORRECT face is blocked for that address+USN while locked", mark("1BM23IS001",b"REG-1BM23IS001").status_code==429)
check("  ...another USN from the same address is unaffected", mark("1BM23IS002",b"REG-1BM23IS002").status_code==200)
check("  ...same USN from another address is unaffected", mark("1BM23IS001",b"REG-1BM23IS001",ip="127.0.0.2",pas=getpass()).status_code in (200,400,403))
print("== success")
auth.checkin_fail_limiter.reset("127.0.0.1|1BM23IS001")
sid2_pass=getpass()
db.attendance_records.delete_many({"usn":"1BM23IS001"})
mark("1BM23IS001",b"FAR-x",pas=getpass())  # one failure on the books
r=mark("1BM23IS001",b"REG-1BM23IS001",pas=getpass()); check("genuine check-in succeeds and returns the student's name", r.status_code==200 and r.json["name"]=="Asha Rao", r.data)
check("  ...success clears the failure count", auth.checkin_fail_limiter.blocked_for("127.0.0.1|1BM23IS001")==0 and "127.0.0.1|1BM23IS001" not in auth.checkin_fail_limiter._failures)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
