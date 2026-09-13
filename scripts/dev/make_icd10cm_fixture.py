"""Regenerate the ICD-10-CM sample order files under fixtures/terminology/.

The real CDC order file is ~74,000 lines; the fixture is a curated subset in the same fixed-width layout
(`terminology.parse_order_file` reads columns, not delimiters), so anything that works on the sample works on the
full release. Coverage: hip, knee and shoulder replacement and their aftercare/complications, the osteoarthritis
that leads to them, and the common sports injuries of the shoulder, knee, hip/thigh, ankle/leg, elbow/wrist, spine
and head. Laterality variants (right/left/bilateral/unspecified) and, for injuries, both the initial (A) and
subsequent (D) encounter characters — rehab is mostly the subsequent encounter.

Category codes (no 7th character, no laterality) are included with billable=0: they resolve as a search scope,
never as a billable diagnosis, exactly as the real file marks them.

Run: uv run python scripts/dev/make_icd10cm_fixture.py
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "fixtures" / "terminology"

SIDES3 = [("1", "right"), ("2", "left"), ("9", "unspecified")]
SIDES_BI = [("1", "right"), ("2", "left"), ("3", "bilateral"), ("9", "unspecified")]
ENC = [("A", "initial encounter"), ("D", "subsequent encounter")]

rows: list[tuple[str, int, str]] = []  # (code without dot, billable, long description)


def cat(code: str, desc: str) -> None:
    rows.append((code, 0, desc))


def leaf(code: str, desc: str) -> None:
    rows.append((code, 1, desc))


def sided(stem: str, template: str, sides=SIDES3, enc: bool = False) -> None:
    """template uses {side}; stem is the code prefix before the laterality digit."""
    for digit, side in sides:
        if enc:
            for ch, when in ENC:
                leaf(f"{stem}{digit}{ch}", f"{template.format(side=side)}, {when}")
        else:
            leaf(f"{stem}{digit}", template.format(side=side))


def encs(code: str, desc: str) -> None:
    for ch, when in ENC:
        leaf(f"{code}{ch}", f"{desc}, {when}")


# ------------------------------------------------------------------ existing sample entries (kept first)
cat("M750", "Adhesive capsulitis of shoulder")
leaf("M7500", "Adhesive capsulitis of unspecified shoulder")
leaf("M7501", "Adhesive capsulitis of right shoulder")
leaf("M7502", "Adhesive capsulitis of left shoulder")
cat("S8341", "Sprain of medial collateral ligament of knee")
sided("S8341", "Sprain of medial collateral ligament of {side} knee", enc=True)
cat("S7631", "Strain of muscle, fascia and tendon of the posterior muscle group at thigh level")
sided("S7631", "Strain of muscle, fascia and tendon of the posterior muscle group at thigh level, {side} thigh", enc=True)

# ------------------------------------------------------------------ joint replacement: presence, aftercare, complications
cat("Z966", "Presence of orthopedic joint implants")
cat("Z9661", "Presence of artificial shoulder joint")
leaf("Z96611", "Presence of right artificial shoulder joint")
leaf("Z96612", "Presence of left artificial shoulder joint")
leaf("Z96619", "Presence of unspecified artificial shoulder joint")
cat("Z9664", "Presence of artificial hip joint")
sided("Z9664", "Presence of {side} artificial hip joint", SIDES_BI)
cat("Z9665", "Presence of artificial knee joint")
sided("Z9665", "Presence of {side} artificial knee joint", SIDES_BI)
leaf("Z471", "Aftercare following joint replacement surgery")
leaf("Z472", "Encounter for removal of internal fixation device")
leaf("Z4731", "Aftercare following explantation of shoulder joint prosthesis")
leaf("Z4732", "Aftercare following explantation of hip joint prosthesis")
leaf("Z4733", "Aftercare following explantation of knee joint prosthesis")
leaf("Z4789", "Encounter for other orthopedic aftercare")
leaf("Z48812", "Encounter for surgical aftercare following surgery on the musculoskeletal system")
leaf("Z5189", "Encounter for other specified aftercare")
leaf("Z8961", "Acquired absence of hip joint")  # not a laterality code in ICD; kept as the category-like leaf
cat("T840", "Mechanical complication of internal joint prosthesis")
for code, desc in [
    ("T84010", "Broken internal right hip prosthesis"),
    ("T84011", "Broken internal left hip prosthesis"),
    ("T84012", "Broken internal right knee prosthesis"),
    ("T84013", "Broken internal left knee prosthesis"),
    ("T84020", "Dislocation of internal right hip prosthesis"),
    ("T84021", "Dislocation of internal left hip prosthesis"),
    ("T84022", "Instability of internal right knee prosthesis"),
    ("T84023", "Instability of internal left knee prosthesis"),
    ("T84030", "Mechanical loosening of unspecified internal prosthetic joint"),
    ("T84031", "Mechanical loosening of internal right hip prosthetic joint"),
    ("T84032", "Mechanical loosening of internal left hip prosthetic joint"),
    ("T84033", "Mechanical loosening of internal right knee prosthetic joint"),
    ("T84034", "Mechanical loosening of internal left knee prosthetic joint"),
    ("T84050", "Periprosthetic osteolysis of unspecified internal prosthetic joint"),
    ("T84051", "Periprosthetic osteolysis of internal right hip prosthetic joint"),
    ("T84052", "Periprosthetic osteolysis of internal left hip prosthetic joint"),
    ("T84053", "Periprosthetic osteolysis of internal right knee prosthetic joint"),
    ("T84054", "Periprosthetic osteolysis of internal left knee prosthetic joint"),
    ("T8451X", "Infection and inflammatory reaction due to internal right hip prosthesis"),
    ("T8452X", "Infection and inflammatory reaction due to internal left hip prosthesis"),
    ("T8453X", "Infection and inflammatory reaction due to internal right knee prosthesis"),
    ("T8454X", "Infection and inflammatory reaction due to internal left knee prosthesis"),
    ("T8484X", "Pain due to internal orthopedic prosthetic devices, implants and grafts"),
    ("T8481X", "Embolism due to internal orthopedic prosthetic devices, implants and grafts"),
]:
    encs(code, desc)
cat("M97", "Periprosthetic fracture around internal prosthetic joint")
for code, desc in [
    ("M9701X", "Periprosthetic fracture around internal prosthetic right hip joint"),
    ("M9702X", "Periprosthetic fracture around internal prosthetic left hip joint"),
    ("M9711X", "Periprosthetic fracture around internal prosthetic right knee joint"),
    ("M9712X", "Periprosthetic fracture around internal prosthetic left knee joint"),
    ("M9731X", "Periprosthetic fracture around internal prosthetic right shoulder joint"),
    ("M9732X", "Periprosthetic fracture around internal prosthetic left shoulder joint"),
]:
    encs(code, desc)

# ------------------------------------------------------------------ osteoarthritis and avascular necrosis leading to replacement
cat("M16", "Osteoarthritis of hip")
leaf("M160", "Bilateral primary osteoarthritis of hip")
leaf("M1610", "Unilateral primary osteoarthritis, unspecified hip")
leaf("M1611", "Unilateral primary osteoarthritis, right hip")
leaf("M1612", "Unilateral primary osteoarthritis, left hip")
leaf("M162", "Bilateral osteoarthritis resulting from hip dysplasia")
leaf("M1630", "Unilateral osteoarthritis resulting from hip dysplasia, unspecified hip")
leaf("M1631", "Unilateral osteoarthritis resulting from hip dysplasia, right hip")
leaf("M1632", "Unilateral osteoarthritis resulting from hip dysplasia, left hip")
leaf("M164", "Bilateral post-traumatic osteoarthritis of hip")
leaf("M1650", "Unilateral post-traumatic osteoarthritis, unspecified hip")
leaf("M1651", "Unilateral post-traumatic osteoarthritis, right hip")
leaf("M1652", "Unilateral post-traumatic osteoarthritis, left hip")
leaf("M166", "Other bilateral secondary osteoarthritis of hip")
leaf("M167", "Other unilateral secondary osteoarthritis of hip")
leaf("M169", "Osteoarthritis of hip, unspecified")
cat("M17", "Osteoarthritis of knee")
leaf("M170", "Bilateral primary osteoarthritis of knee")
leaf("M1710", "Unilateral primary osteoarthritis, unspecified knee")
leaf("M1711", "Unilateral primary osteoarthritis, right knee")
leaf("M1712", "Unilateral primary osteoarthritis, left knee")
leaf("M172", "Bilateral post-traumatic osteoarthritis of knee")
leaf("M1730", "Unilateral post-traumatic osteoarthritis, unspecified knee")
leaf("M1731", "Unilateral post-traumatic osteoarthritis, right knee")
leaf("M1732", "Unilateral post-traumatic osteoarthritis, left knee")
leaf("M174", "Other bilateral secondary osteoarthritis of knee")
leaf("M175", "Other unilateral secondary osteoarthritis of knee")
leaf("M179", "Osteoarthritis of knee, unspecified")
cat("M1901", "Primary osteoarthritis, shoulder")
sided("M1901", "Primary osteoarthritis, {side} shoulder")
cat("M1911", "Post-traumatic osteoarthritis, shoulder")
sided("M1911", "Post-traumatic osteoarthritis, {side} shoulder")
cat("M8705", "Idiopathic aseptic necrosis of pelvis and femur")
leaf("M87050", "Idiopathic aseptic necrosis of pelvis")
leaf("M87051", "Idiopathic aseptic necrosis of right femur")
leaf("M87052", "Idiopathic aseptic necrosis of left femur")
leaf("M87059", "Idiopathic aseptic necrosis of unspecified femur")
leaf("M87011", "Idiopathic aseptic necrosis of right shoulder")
leaf("M87012", "Idiopathic aseptic necrosis of left shoulder")
cat("M1A", "Chronic gout")  # kept minimal on purpose; rheumatology is outside this fixture's brief

# ------------------------------------------------------------------ joint pain, stiffness, contracture, instability (pre/post-op presentations)
cat("M255", "Pain in joint")
sided("M2551", "Pain in {side} shoulder")
sided("M2555", "Pain in {side} hip")
sided("M2556", "Pain in {side} knee")
sided("M2557", "Pain in {side} ankle and joints of {side} foot")
sided("M2561", "Stiffness of {side} shoulder, not elsewhere classified")
sided("M2565", "Stiffness of {side} hip, not elsewhere classified")
sided("M2566", "Stiffness of {side} knee, not elsewhere classified")
sided("M2451", "Contracture, {side} shoulder")
sided("M2455", "Contracture, {side} hip")
sided("M2456", "Contracture, {side} knee")
sided("M2441", "Recurrent dislocation, {side} shoulder")
sided("M2485", "Other specific joint derangements of {side} hip, not elsewhere classified")
sided("M2415", "Other articular cartilage disorders, {side} hip")
sided("M2425", "Disorder of ligament, {side} hip")
cat("M235", "Chronic instability of knee")
leaf("M2350", "Chronic instability of knee, unspecified knee")
leaf("M2351", "Chronic instability of knee, right knee")
leaf("M2352", "Chronic instability of knee, left knee")
sided("M2483", "Other specific joint derangements of {side} ankle, not elsewhere classified")

# ------------------------------------------------------------------ shoulder: rotator cuff, impingement, tendinopathy, instability, fractures
cat("M751", "Rotator cuff tear or rupture, not specified as traumatic")
for sub, desc in [
    ("0", "Unspecified rotator cuff tear or rupture"),
    ("1", "Incomplete rotator cuff tear or rupture"),
    ("2", "Complete rotator cuff tear or rupture"),
]:
    for digit, side in [("0", "unspecified"), ("1", "right"), ("2", "left")]:
        leaf(f"M751{sub}{digit}", f"{desc} of {side} shoulder, not specified as traumatic")
for sub, desc in [
    ("2", "Bicipital tendinitis"),
    ("3", "Calcific tendinitis of"),
    ("4", "Impingement syndrome of"),
    ("5", "Bursitis of"),
    ("8", "Other shoulder lesions,"),
]:
    for digit, side in [("0", "unspecified"), ("1", "right"), ("2", "left")]:
        leaf(f"M75{sub}{digit}", f"{desc} {side} shoulder".replace("tendinitis, ", "tendinitis, "))
cat("S430", "Subluxation and dislocation of shoulder joint")
for stem, desc in [
    ("S4300", "Unspecified subluxation and dislocation of"),
    ("S4301", "Anterior subluxation and dislocation of"),
    ("S4302", "Posterior subluxation and dislocation of"),
]:
    for digit, side in [("1", "right"), ("2", "left"), ("3", "unspecified")]:
        encs(f"{stem}{digit}", f"{desc} {side} shoulder joint")
cat("S431", "Subluxation and dislocation of acromioclavicular joint")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S4310{digit}", f"Unspecified dislocation of {side} acromioclavicular joint")
cat("S434", "Sprain of shoulder joint")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S4340{digit}", f"Unspecified sprain of {side} shoulder joint")
    encs(f"S4342{digit}", f"Sprain of {side} rotator cuff capsule")
    encs(f"S4343{digit}", f"Superior glenoid labrum lesion of {side} shoulder")
for digit, side in [("0", "unspecified"), ("1", "right"), ("2", "left")]:
    encs(f"S435{digit}X", f"Sprain of {side} acromioclavicular joint")
cat("S460", "Injury of muscle(s) and tendon(s) of the rotator cuff of shoulder")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S4601{digit}", f"Strain of muscle(s) and tendon(s) of the rotator cuff of {side} shoulder")
    encs(f"S4602{digit}", f"Laceration of muscle(s) and tendon(s) of the rotator cuff of {side} shoulder")
    encs(f"S4611{digit}", f"Strain of muscle, fascia and tendon of long head of biceps, {side} arm")
    encs(f"S4200{digit}", f"Fracture of unspecified part of {side} clavicle")
    encs(f"S4220{digit}", f"Unspecified fracture of upper end of {side} humerus")
    encs(f"S4230{digit}", f"Unspecified fracture of shaft of humerus, {side} arm")

# ------------------------------------------------------------------ knee: ligaments, meniscus, patella, tendinopathy, fractures
cat("S835", "Sprain of cruciate ligament of knee")
for stem, desc in [
    ("S8350", "Sprain of unspecified cruciate ligament of"),
    ("S8351", "Sprain of anterior cruciate ligament of"),
    ("S8352", "Sprain of posterior cruciate ligament of"),
]:
    for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
        encs(f"{stem}{digit}", f"{desc} {side} knee")
cat("S8342", "Sprain of lateral collateral ligament of knee")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S8342{digit}", f"Sprain of lateral collateral ligament of {side} knee")
cat("S832", "Tear of meniscus, current injury")
for stem, desc in [
    ("S8320", "Unspecified tear of unspecified meniscus, current injury,"),
    ("S8321", "Bucket-handle tear of medial meniscus, current injury,"),
    ("S8322", "Peripheral tear of medial meniscus, current injury,"),
    ("S8323", "Complex tear of medial meniscus, current injury,"),
    ("S8324", "Other tear of medial meniscus, current injury,"),
    ("S8325", "Bucket-handle tear of lateral meniscus, current injury,"),
    ("S8326", "Peripheral tear of lateral meniscus, current injury,"),
    ("S8327", "Complex tear of lateral meniscus, current injury,"),
    ("S8328", "Other tear of lateral meniscus, current injury,"),
]:
    for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
        encs(f"{stem}{digit}", f"{desc} {side} knee")
cat("S830", "Subluxation and dislocation of patella")
for digit, side, what in [
    ("1", "right", "subluxation"),
    ("2", "left", "subluxation"),
    ("3", "unspecified", "subluxation"),
    ("4", "right", "dislocation"),
    ("5", "left", "dislocation"),
    ("6", "unspecified", "dislocation"),
]:
    encs(f"S8300{digit}", f"Unspecified {what} of {side} patella")
for digit, side in [("1", "right"), ("2", "left"), ("0", "unspecified")]:
    encs(f"S836{digit}X", f"Sprain of the superior tibiofibular joint and ligament, {side} knee")
    encs(f"S839{digit}X", f"Sprain of unspecified site of {side} knee")
cat("M222", "Patellofemoral disorders")
leaf("M222X1", "Patellofemoral disorders, right knee")
leaf("M222X2", "Patellofemoral disorders, left knee")
leaf("M222X9", "Patellofemoral disorders, unspecified knee")
leaf("M2240", "Chondromalacia patellae, unspecified knee")
leaf("M2241", "Chondromalacia patellae, right knee")
leaf("M2242", "Chondromalacia patellae, left knee")
leaf("M2250", "Chronic instability of patella, unspecified knee")
leaf("M2251", "Chronic instability of patella, right knee")
leaf("M2252", "Chronic instability of patella, left knee")
sided("M9426", "Chondromalacia, {side} knee")
for stem, desc in [
    ("M765", "Patellar tendinitis,"),
    ("M763", "Iliotibial band syndrome,"),
    ("M704", "Prepatellar bursitis,"),
    ("M705", "Other bursitis of knee,"),
]:
    for digit, side in [("0", "unspecified"), ("1", "right"), ("2", "left")]:
        leaf(f"{stem}{digit}", f"{desc} {side} knee" if stem != "M763" else f"{desc} {side} leg")
cat("M232", "Derangement of meniscus due to old tear or injury")
for stem, desc in [
    ("M2320", "Derangement of unspecified meniscus due to old tear or injury,"),
    ("M2322", "Derangement of posterior horn of medial meniscus due to old tear or injury,"),
    ("M2323", "Derangement of other medial meniscus due to old tear or injury,"),
    ("M2326", "Derangement of other lateral meniscus due to old tear or injury,"),
]:
    for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
        leaf(f"{stem}{digit}", f"{desc} {side} knee")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S8200{digit}", f"Unspecified fracture of {side} patella")
    encs(f"S8210{digit}", f"Unspecified fracture of upper end of {side} tibia")
    encs(f"S7240{digit}", f"Unspecified fracture of lower end of {side} femur")

# ------------------------------------------------------------------ hip, thigh and groin
cat("S760", "Injury of muscle, fascia and tendon of hip")
for stem, desc in [
    ("S7601", "Strain of muscle, fascia and tendon of"),
    ("S7611", "Strain of quadriceps muscle, fascia and tendon,"),
    ("S7621", "Strain of adductor muscle, fascia and tendon of"),
]:
    for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
        tail = f"{side} hip" if stem == "S7601" else (f"{side} thigh" if stem == "S7611" else f"{side} thigh")
        encs(f"{stem}{digit}", f"{desc} {tail}")
cat("S731", "Sprain of hip")
for stem, desc in [
    ("S7310", "Unspecified sprain of"),
    ("S7311", "Iliofemoral ligament sprain of"),
    ("S7312", "Ischiocapsular ligament sprain of"),
    ("S7319", "Other sprain of"),
]:
    for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
        encs(f"{stem}{digit}", f"{desc} {side} hip")
cat("S730", "Subluxation and dislocation of hip")
for digit, side, what in [
    ("1", "right", "subluxation"),
    ("2", "left", "subluxation"),
    ("3", "unspecified", "subluxation"),
    ("4", "right", "dislocation"),
    ("5", "left", "dislocation"),
    ("6", "unspecified", "dislocation"),
]:
    encs(f"S7300{digit}", f"Unspecified {what} of {side} hip")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S7200{digit}", f"Fracture of unspecified part of neck of {side} femur")
    encs(f"S7210{digit}", f"Unspecified trochanteric fracture of {side} femur")
    encs(f"S7230{digit}", f"Unspecified fracture of shaft of {side} femur")
for stem, desc in [
    ("M706", "Trochanteric bursitis,"),
    ("M707", "Other bursitis of"),
    ("M760", "Gluteal tendinitis,"),
    ("M761", "Psoas tendinitis,"),
    ("M762", "Iliac crest spur,"),
]:
    for digit, side in [("0", "unspecified"), ("1", "right"), ("2", "left")]:
        leaf(f"{stem}{digit}", f"{desc} {side} hip")
leaf("M7989", "Other specified soft tissue disorders")
encs("S39012", "Strain of muscle, fascia and tendon of lower back")
encs("S39011", "Strain of muscle, fascia and tendon of abdomen")
encs("S39013", "Strain of muscle, fascia and tendon of pelvis")

# ------------------------------------------------------------------ ankle, lower leg, foot
cat("S934", "Sprain of ankle")
for stem, desc in [
    ("S9340", "Sprain of unspecified ligament of"),
    ("S9341", "Sprain of deltoid ligament of"),
    ("S9342", "Sprain of calcaneofibular ligament of"),
    ("S9343", "Sprain of tibiofibular ligament of"),
    ("S9349", "Sprain of other ligament of"),
]:
    for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
        encs(f"{stem}{digit}", f"{desc} {side} ankle")
cat("S860", "Injury of Achilles tendon")
for stem, desc in [("S8601", "Strain of"), ("S8602", "Laceration of")]:
    for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
        encs(f"{stem}{digit}", f"{desc} {side} Achilles tendon")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S8611{digit}", f"Strain of other muscle(s) and tendon(s) of posterior muscle group at lower leg level, {side} leg")
    encs(f"S8631{digit}", f"Strain of muscle(s) and tendon(s) of peroneal muscle group at lower leg level, {side} leg")
for digit, side in [("1", "right"), ("2", "left"), ("3", "unspecified")]:
    encs(f"S826{digit}X", f"Displaced fracture of lateral malleolus of {side} fibula")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S9230{digit}", f"Fracture of unspecified metatarsal bone(s), {side} foot")
for stem, desc in [("M766", "Achilles tendinitis,"), ("M767", "Peroneal tendinitis,")]:
    for digit, side in [("0", "unspecified"), ("1", "right"), ("2", "left")]:
        leaf(f"{stem}{digit}", f"{desc} {side} leg")
sided("M7681", "Anterior tibial syndrome, {side} leg", [("1", "right"), ("2", "left"), ("9", "unspecified")])
sided("M7682", "Posterior tibial tendinitis, {side} leg", [("1", "right"), ("2", "left"), ("9", "unspecified")])
leaf("M722", "Plantar fascial fibromatosis")
leaf("M7740", "Metatarsalgia, unspecified foot")
leaf("M7741", "Metatarsalgia, right foot")
leaf("M7742", "Metatarsalgia, left foot")

# ------------------------------------------------------------------ elbow, wrist, hand
for stem, desc in [("M770", "Medial epicondylitis,"), ("M771", "Lateral epicondylitis,")]:
    for digit, side in [("0", "unspecified"), ("1", "right"), ("2", "left")]:
        leaf(f"{stem}{digit}", f"{desc} {side} elbow")
cat("S534", "Sprain of elbow")
for stem, desc in [
    ("S5340", "Unspecified sprain of"),
    ("S5342", "Radial collateral ligament sprain of"),
    ("S5343", "Ulnar collateral ligament sprain of"),
]:
    for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
        encs(f"{stem}{digit}", f"{desc} {side} elbow")
for digit, side in [("1", "right"), ("2", "left"), ("9", "unspecified")]:
    encs(f"S6350{digit}", f"Unspecified sprain of {side} wrist")
    encs(f"S6200{digit}", f"Unspecified fracture of navicular [scaphoid] bone of {side} wrist")
    encs(f"S5250{digit}", f"Unspecified fracture of the lower end of {side} radius")
    encs(f"S6361{digit}", f"Unspecified sprain of {side} thumb")

# ------------------------------------------------------------------ spine and head (sports)
encs("S134XX", "Sprain of ligaments of cervical spine")
encs("S161XX", "Strain of muscle, fascia and tendon at neck level")
encs("S335XX", "Sprain of ligaments of lumbar spine")
encs("S336XX", "Sprain of sacroiliac joint")
leaf("M5450", "Low back pain, unspecified")
leaf("M5451", "Vertebrogenic low back pain")
leaf("M5459", "Other low back pain")
leaf("M542", "Cervicalgia")
leaf("M5126", "Other intervertebral disc displacement, lumbar region")
leaf("M5127", "Other intervertebral disc displacement, lumbosacral region")
leaf("M5416", "Radiculopathy, lumbar region")
leaf("M5417", "Radiculopathy, lumbosacral region")
leaf("M4726", "Other spondylosis with radiculopathy, lumbar region")
leaf("M4316", "Spondylolisthesis, lumbar region")
encs("S060X0", "Concussion without loss of consciousness")
encs("S060X1", "Concussion with loss of consciousness of 30 minutes or less")
encs("S060X9", "Concussion with loss of consciousness of unspecified duration")

# ------------------------------------------------------------------ general soft tissue
leaf("M7910", "Myalgia, unspecified site")
leaf("M7918", "Myalgia, other site")
leaf("M62838", "Other muscle spasm")
leaf("M6281", "Muscle weakness (generalized)")
leaf("M6250", "Muscle wasting and atrophy, not elsewhere classified, unspecified site")
leaf("R262", "Difficulty in walking, not elsewhere classified")
leaf("R2689", "Other abnormalities of gait and mobility")
leaf("R2681", "Unsteadiness on feet")
leaf("Z9181", "History of falling")


def render() -> str:
    # dedupe by code, keep first (the hand-kept sample entries come first)
    seen: set[str] = set()
    lines = []
    n = 0
    for code, billable, desc in rows:
        if code in seen:
            continue
        seen.add(code)
        n += 1
        short = desc[:60]
        lines.append(f"{n:05d} {code:<7} {billable} {short:<60} {desc}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = render()
    for name in ("icd10cm_order_sample_FY26.txt", "icd10cm_order_sample_FY27.txt"):
        (OUT / name).write_text(text)
    print(f"wrote {text.count(chr(10))} codes to {OUT}")
