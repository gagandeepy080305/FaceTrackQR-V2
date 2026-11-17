import io, os, sys, csv, tempfile
APP=os.environ["FT_APP_DIR"]; FAKE=os.environ["FT_FAKE_DIR"]
sys.dont_write_bytecode = True
sys.path.insert(0, APP); sys.path.insert(0, FAKE)
from tools import accuracy_report as ar
ok=fail=0
def check(n,cond,extra=""):
    global ok,fail
    if cond: ok+=1; print("PASS",n)
    else: fail+=1; print("FAIL",n,extra)

# stand-in model distances: NEAR-* = 0.27, FAR-* = 0.43, anything else different = 0.9
d=tempfile.mkdtemp()
def photo(name,data):
    p=os.path.join(d,name); open(p,"wb").write(data); return p
rows=[
 {"path":photo("a_ref.jpg",b"A-REF"),"person":"P1","kind":"ref"},
 {"path":photo("a1.jpg",b"NEAR-a1"),"person":"P1","kind":"selfie"},
 {"path":photo("a2.jpg",b"FAR-a2"),"person":"P1","kind":"selfie"},
 {"path":photo("b_ref.jpg",b"B-REF"),"person":"P2","kind":"ref"},
 {"path":photo("b1.jpg",b"NEAR-b1"),"person":"P2","kind":"selfie"},
 {"path":photo("same.jpg",b"A-REF"),"person":"P1","kind":"selfie"},   # byte-identical to a ref: must be skipped
]
pairs=ar.build_pairs(rows)
check("every ref is paired with every selfie; the byte-identical pair is skipped (2 refs x 4 selfies - 1 = 7)", len(pairs)==7, len(pairs))
check("same person = genuine, different person = impostor", sum(1 for *_,g in pairs if g)==3 and sum(1 for *_,g in pairs if not g)==4)
g,i,failed=ar.measure_pairs(pairs,"Facenet512")
check("distances come from the model (genuine 0.27/0.27/0.43; impostor 0.27/0.27/0.43/0.9)", sorted(g)==[0.27,0.27,0.43] and sorted(i)==[0.27,0.27,0.43,0.9], (g,i))
check("  ...nothing failed", failed==0)
s=ar.describe([0.2,0.3,0.4]); check("describe: n/min/median/max", s=={"n":3,"min":0.2,"median":0.3,"max":0.4}, s)
check("describe of nothing is None", ar.describe([]) is None)
o=ar.outcome_counts([0.10,0.254,0.255,0.30,0.31,0.9],0.30,0.255)
check("outcomes: counted < review line, held from review line to pass mark inclusive, rejected above", o=={"counted":2,"held":2,"rejected":2}, o)
sw={r["threshold"]:r for r in ar.sweep_rows([0.2,0.27,0.43],[0.26,0.5,0.9])}
check("sweep: false reject / false accept per threshold", sw[0.25]["false_reject"]==2 and sw[0.25]["false_accept"]==0 and sw[0.30]["false_reject"]==1 and sw[0.30]["false_accept"]==1 and sw[0.50]["false_accept"]==2, sw)
check("rule of three", ar.rule_of_three(30)==0.1 and ar.rule_of_three(0) is None)
report=ar.build_report(rows,["Facenet512","VGG-Face"],skipped=2)
check("report has a section per model with the right pass marks", "## Facenet512" in report and "## VGG-Face" in report and "Pass mark **0.3**" in report and "Pass mark **0.5**" in report)
check("report states sample sizes and skipped photos", "Pairs measured: **3 genuine**, **4 impostor**" in report and "2 listed photos skipped" in report)
check("report never contains photo paths", d not in report and ".jpg" not in report)
check("Facenet512 table: genuine 0.27 x2 are held, 0.43 rejected, none counted straight away", "| Genuine | 0 (0%) | 2 (67%) | 1 (33%) |" in report, report)
check("zero automatic impostors -> hedged wording, not a claim of 0%", "only shows the true rate is probably below" in report, report[-600:])
# labels file
lab=os.path.join(d,"labels.csv")
with open(lab,"w",newline="") as h:
    w=csv.writer(h); w.writerow(["path","person","kind"])
    w.writerow(["a_ref.jpg","P1","ref"]); w.writerow(["a1.jpg","","selfie"]); w.writerow(["missing.jpg","P1","selfie"]); w.writerow(["a2.jpg","P1","bogus"]); w.writerow(["a2.jpg","P1","selfie"])
rows2,skipped=ar.load_labels(lab,d)
check("labels: unlabelled, missing-file and bad-kind rows are skipped and counted", len(rows2)==2 and skipped==3, (len(rows2),skipped))
# template
os.makedirs(os.path.join(d,"uploads","faces")); os.makedirs(os.path.join(d,"uploads","checkins","s1"))
open(os.path.join(d,"uploads","faces","1BM23IS001.jpg"),"wb").write(b"x"); open(os.path.join(d,"uploads","checkins","s1","1BM23IS001.jpg"),"wb").write(b"y")
tpl=os.path.join(d,"tpl.csv"); n=ar.write_template(tpl,d)
t=list(csv.DictReader(open(tpl)))
check("template lists stored photos with blank person and the right kind", n==2 and {(r["kind"],r["person"]) for r in t}=={("ref",""),("selfie","")}, t)
only_refs=os.path.join(d,"only_refs.csv")
with open(only_refs,"w",newline="") as h:
    w=csv.writer(h); w.writerow(["path","person","kind"]); w.writerow(["a_ref.jpg","P1","ref"])
check("CLI refuses to run without both a ref and a selfie (exit 1)", ar.main(["--labels",only_refs,"--root",d])==1)
out=os.path.join(d,"out.md")
full=os.path.join(d,"full.csv")
with open(full,"w",newline="") as h:
    w=csv.writer(h); w.writerow(["path","person","kind"]); w.writerow(["a_ref.jpg","P1","ref"]); w.writerow(["a1.jpg","P1","selfie"]); w.writerow(["b_ref.jpg","P2","ref"]); w.writerow(["b1.jpg","P2","selfie"])
check("CLI writes the report file", ar.main(["--labels",full,"--root",d,"--out",out])==0 and "# Face-match accuracy report" in open(out).read())
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
