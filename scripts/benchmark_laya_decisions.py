"""Small, reproducible local-only sanity set; not a domain calibration dataset."""
from __future__ import annotations

import json
import os
import time
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from laya import Router
from linkedin_connector.local_decisions import ROLES


CASES = [
    {"role":"software-data-ai", "job":"Data analyst: build Python dashboards, automate reporting and analyse operational data.", "company":"Acme", "title":"Data Analyst", "cv":"Built Python dashboards and automated recurring operations reports.", "style":"Dear team, I am applying for the analyst role. I built Python dashboards to improve recurring reporting.", "duplicate":"same"},
    {"role":"software-data-ai", "job":"Backend software engineer: design REST APIs, maintain distributed Python services and improve tests.", "company":"Acme", "title":"Backend Software Engineer", "cv":"Designed REST APIs in Python and improved automated integration tests.", "style":"Dear hiring team, I am applying for the backend role. I designed and tested Python APIs used by internal teams.", "duplicate":"same"},
    {"role":"engineering-research", "job":"Biomedical engineer: prototype medical devices, run verification studies and document design controls.", "company":"MedCo", "title":"Biomedical Engineer", "cv":"Designed verification protocols and analysed biomedical device test results.", "style":"Dear team, I am applying for the engineering role. I documented verification protocols and analysed device test results.", "duplicate":"same"},
    {"role":"engineering-research", "job":"Research scientist in biomaterials: plan laboratory experiments and publish research findings.", "company":"MedCo", "title":"Research Scientist", "cv":"Planned laboratory studies and analysed biomaterials research data.", "style":"Dear team, I am applying for the research role. I planned studies and analysed experimental data.", "duplicate":"distinct"},
    {"role":"healthcare-pharma", "job":"Pharmacovigilance specialist: assess safety cases and prepare regulatory submissions.", "company":"Pharma", "title":"Drug Safety Specialist", "cv":"Reviewed adverse event case files and prepared safety documentation.", "style":"Dear hiring manager, I am applying for the pharmacovigilance role. I reviewed safety records and prepared compliant documentation.", "duplicate":"same"},
    {"role":"healthcare-pharma", "job":"Clinical research associate: monitor trial sites, check study documentation and support GCP compliance.", "company":"Pharma", "title":"Clinical Research Associate", "cv":"Supported clinical study documentation and checked records for completeness.", "style":"Dear team, I am applying for the clinical research role. I supported study documentation and checked records.", "duplicate":"distinct"},
    {"role":"sales-marketing", "job":"B2B sales manager: manage strategic accounts, develop sales plans and negotiate commercial agreements.", "company":"RetailCo", "title":"Sales Manager", "cv":"Managed B2B customer accounts and developed account plans.", "style":"Dear team, I am applying for the sales role. I managed B2B accounts and worked with customers on account plans.", "duplicate":"same"},
    {"role":"operations-business", "job":"Operations project manager: coordinate suppliers, manage timelines and improve business processes.", "company":"RetailCo", "title":"Operations Project Manager", "cv":"Coordinated supplier projects and improved internal business processes.", "style":"Dear team, I am applying for the operations role. I coordinated supplier projects and improved internal processes.", "duplicate":"distinct"},
    {"role":"operations-business", "job":"Finance controller: prepare budgets, reconcile accounts and report monthly financial performance.", "company":"RetailCo", "title":"Finance Controller", "cv":"Prepared department budgets and monthly financial performance reports.", "style":"Dear team, I am applying for the finance role. I prepared budgets and monthly reports for management.", "duplicate":"distinct"},
    {"role":"other", "job":"Electrician: install and maintain industrial electrical systems and troubleshoot wiring faults.", "company":"BuildCo", "title":"Industrial Electrician", "cv":"Built data dashboards and automated business reports using Python.", "style":"Dear team, I am applying for a software role. I built Python dashboards and automated business reports.", "duplicate":"distinct"},
]

router = Router(device="cpu", max_loaded=1)
questions = {
    "role": {"type":"choice", "instructions":"What is the primary function of this job? Classify only the job requirements.", "criteria": ROLES},
    "candidate_match": {"type":"choice", "instructions":"How strongly does the candidate evidence match the explicit job requirements? Judge the provided evidence, not inferred potential.", "criteria":{"strong":"Concrete experience directly demonstrates multiple stated requirements.", "partial":"Some relevant experience but important stated requirements are not evidenced.", "weak":"The evidence is unrelated to the requirements or gives no usable example."}},
    "style_relevance": {"type":"choice", "instructions":"How useful is this cover letter as a STYLE example for this exact role? Ignore candidate facts as evidence.", "criteria":{"relevant":"Professional structure, tone and evidence-detail level fit the target role.", "irrelevant":"Structure or content style is a poor reference for this target role.", "uncertain":"Not enough information to judge stylistic relevance."}},
    "same_vacancy": {"type":"choice", "instructions":"Do these postings represent the same underlying vacancy? Compare requirements and role, not just employer.", "criteria":{"same":"The postings clearly describe the same vacancy.", "different":"The postings describe different jobs.", "uncertain":"The information is insufficient to decide."}},
}

requests = []
for index, case in enumerate(CASES):
    # Make the existing posting exact or a plausible sibling from the same company.
    if case["duplicate"] == "same":
        other = {"company":case["company"], "title":case["title"], "description":case["job"].replace(":", " —")}
    else:
        other = {"company":case["company"], "title":case["title"] + " (different function)", "description":"Different department with unrelated day-to-day responsibilities."}
    requests.append({"state": {
        "job": {"company":case["company"], "title":case["title"], "description":case["job"]},
        "candidate_evidence":case["cv"], "style_example":case["style"], "existing_job":other,
    }, "questions":questions, "model":"multilingual", "max_len":8192})

started = time.monotonic()
predictions = router.predict_batch(requests, batch_size=len(requests), sort_by_length=True)
elapsed = time.monotonic() - started
metrics = {key: {"correct":0, "total":len(CASES), "results":[]} for key in questions}
for index, (case, prediction) in enumerate(zip(CASES, predictions)):
    expected = {"role":case["role"], "candidate_match": "weak" if index == 9 else "strong",
                "style_relevance":"relevant", "same_vacancy":case["duplicate"]}
    # Case design includes different postings from the same company; their function differs.
    if case["duplicate"] == "distinct":
        expected["same_vacancy"] = "different"
    answers = prediction["answers"]
    for dimension, label in expected.items():
        answer = answers[dimension]
        choice = answer["choice"]
        metrics[dimension]["correct"] += choice == label
        metrics[dimension]["results"].append({"case":index+1, "expected":label, "choice":choice,
                                               "answer_confidence":answer.get("answer_confidence")})
report = {"model":"laya-multilingual", "cases":len(CASES), "batch_seconds":round(elapsed, 2),
          "domains":{name:{"correct":value["correct"], "total":value["total"],
                            "accuracy":round(value["correct"]/value["total"], 3),
                            "results":value["results"]} for name,value in metrics.items()},
          "warning":"Synthetic 10-case sanity set only; results are not a domain calibration or hiring metric."}
print(json.dumps(report, ensure_ascii=False, indent=2))
