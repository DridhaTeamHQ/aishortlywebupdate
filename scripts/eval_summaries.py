"""Run the summarizer over a fixed set of source articles and dump results to JSON.

Used to evaluate writing quality (title-body coherence, no verbatim copy,
strong lede, framing) against the gpt-5-mini pipeline.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from core.intelligence.summarize import Summarizer  # noqa: E402

ARTICLES = [
    ("Indian Railways to introduce AI waitlist forecasts",
     "Indian Railways is set to introduce an AI-powered ticket reservation system that will predict the chances of waitlist confirmation at the time of booking. Expected to roll out from August 2026, the upgrade aims to improve travel planning and booking efficiency by offering passengers smarter insights into ticket availability."),
    ("Hardik Pandya cleared for Afghanistan ODI series",
     "Hardik Pandya is set to return for the three-match ODI series against Afghanistan starting Sunday in Dharamsala. Cleared by the BCCI's Centre of Excellence sports science team, the 32-year-old had missed multiple IPL matches for Mumbai Indians due to back spasms. Following a brief holiday, Pandya completed intensive five-day match simulations and bowled full 10-over quotas."),
    ("India condemns Pakistan at UN over Fitna al Hindustan label",
     "India condemned Pakistan at the UN for state-sponsored disinformation after Islamabad labeled domestic militant outfits Fitna al Hindustan to blame New Delhi. Indian Envoy Parvathaneni Harish slammed the move as a factory of hate to mask internal crises. India also blasted Pakistan's cross-border airstrikes in Afghanistan, citing UN records of heavy civilian casualties."),
    ("AAP confirms exit from INDIA bloc, demands agenda clarity",
     "The Aam Aadmi Party has officially exited the opposition INDIA bloc, confirming its departure by skipping Monday's alliance meeting. AAP Rajya Sabha MP Sanjay Singh validated the decision during a press conference, stating, AAP is no longer part of the INDIA alliance. He also urged the remaining member political parties to clearly define their legislative agenda."),
    ("OpenAI moves toward potential IPO",
     "OpenAI, the company behind ChatGPT, has confidentially filed for a potential initial public offering (IPO) in the U.S., marking its first formal step toward going public. The filing does not confirm timing, and the listing may still take time. The move comes amid growing competition in the fast-expanding AI industry."),
    ("Final throes: Trump signals breakthrough on nuclear deal",
     "US President Trump stated Washington is in the final throes of securing a landmark deal with Iran to prevent it from acquiring nuclear weapons, expecting clarity within days. Trump predicted the agreement would lower oil prices and reopen the Strait of Hormuz. He also noted a temporary halt to Israel-Iran hostilities after speaking with Israeli PM Benjamin Netanyahu."),
    ("ED conducts raids in Punjab, UP, Delhi-NCR",
     "The Enforcement Directorate conducted searches at six locations across Punjab, Uttar Pradesh, and Delhi-NCR on Tuesday under the Prevention of Money Laundering Act. The raids, targeting residential and commercial properties in Ludhiana, Jalandhar, Bareilly, and Noida, are part of an ongoing money laundering probe linked to Hampton Sky Realty Ltd, officials confirmed."),
    ("Maritime alert: 24 Indians rescued after ship attack",
     "MRCC Mumbai coordinated with Omani authorities to rescue 24 Indian seafarers after a missile attack on Palau-flagged tanker MT Marivex off Masirah coast, Oman. Alert came via a crew member's relative. Oman Maritime Search and Rescue Centre led swift operation, diverting a nearby vessel and deploying two helicopters, ensuring all crew members were safely rescued."),
    ("Iran won't back down, eyes peace and defense: Pezeshkian",
     "Iranian President Masoud Pezeshkian reaffirmed Tehran's commitment to defense and diplomacy, declaring Iran will not yield to threats despite halting military actions against Israel. Our priority is national security, Pezeshkian stated, emphasizing that diplomacy and defense are the two wings of national power. He noted Iran has not abandoned negotiations."),
    ("NEET paper setters to stay in complete isolation for re-exam",
     "The National Testing Agency has enforced strict security for the June 21 NEET re-exam after the May 3 paper leak affected 22 lakh students. Paper setters are kept in complete isolation without phones or internet. The Indian Air Force will transport question papers, while five lakh security personnel and AI-enabled surveillance cameras will monitor the entire process."),
    ("India crush Afghanistan by an innings and 300 runs",
     "India delivered a dominant performance, defeating Afghanistan by an innings and 300 runs. India posted a massive 564/8 declared, before bowling out Afghanistan for 152 and 112 in the two innings. A complete all-round display saw India secure a huge victory and assert their dominance in the match."),
    ("TN CM felicitates Praggnanandhaa with reward",
     "Tamil Nadu Chief Minister honoured chess prodigy R Praggnanandhaa with a 50 lakh rupee reward for his achievements in international chess. During the event, the CM also tried his hand at a friendly game with the young champion, highlighting the growing support for sports talent in the state."),
    ("Big blow to TMC: Jahangir Khan taken into custody",
     "The Special Task Force (STF) of the West Bengal Police arrested TMC leader Jahangir Khan near the Nepal border early Monday. Khan, an aide to MP Abhishek Banerjee and candidate for Falta, faces seven FIRs over post-poll irregularities. The arrest follows the Calcutta High Court's decision to revoke his interim protection, dismissing claims of political vendetta."),
    ("Rupee slides to 95.35 against dollar in trade",
     "The Indian rupee slipped 17 paise to 95.35 against the US dollar in early trade, reflecting continued pressure from global cues. A strong dollar, rising crude oil prices, and ongoing geopolitical tensions weighed on investor sentiment, keeping the currency under strain in foreign exchange markets."),
    ("Israel hits Iran's military targets",
     "Israel struck military targets in central and western Iran early Monday, escalating regional conflict. Iranian state TV reported explosions in Tehran, Isfahan, Karaj, and Tabriz, leading to airspace closures. Israel's military confirmed targeting the regime, while Iran's Revolutionary Guard reported air-launched ballistic missile use. US-Iran ceasefire talks remain stalled."),
    ("France considers further sanctions over West Bank violence",
     "France may impose further sanctions on Israeli settlers amid escalating West Bank violence and illegal settlement growth, Foreign Minister Jean-Noel Barrot announced Sunday. The coordinated European measures build on recent EU sanctions, intensifying Western pressure on Prime Minister Benjamin Netanyahu's administration over policies undermining a two-state solution."),
    ("Antonelli tops Monaco grid with sensational pole lap",
     "Kimi Antonelli produced a sensational final lap to secure pole position for the Monaco Grand Prix, edging out Max Verstappen in a thrilling qualifying session. The young Mercedes driver showcased exceptional pace and composure on the iconic street circuit, marking one of the standout performances of his Formula 1 career and setting up an exciting race day in Monte Carlo."),
    ("JP Nadda fires back at Kharge's healthcare critique",
     "Union Health Minister JP Nadda hit back at Congress chief Mallikarjun Kharge over his criticism of the National Family Health Survey (NFHS-6). Terming Kharge's remarks as half-knowledge, Nadda asserted that the latest data reflects significant healthcare improvements under the Modi government. Kharge had earlier accused the BJP of hiding crucial health data."),
]


def main() -> None:
    s = Summarizer()
    out = []
    for src_title, src_body in ARTICLES:
        try:
            r = s.summarize(src_title, src_body, max_retries=3)
        except Exception as exc:  # noqa: BLE001
            r = {"title": f"<error: {exc}>", "body": ""}
        out.append({
            "source_title": src_title,
            "source_body": src_body,
            "out_title": r.get("title", "") if r else "",
            "out_body": r.get("body", "") if r else "",
            "title_len": len(r.get("title", "")) if r else 0,
            "body_len": len(r.get("body", "")) if r else 0,
        })
        print(f"done: {src_title[:50]}")

    dest = Path(__file__).resolve().parents[1] / "eval_results.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(out)} results -> {dest}")


if __name__ == "__main__":
    main()
