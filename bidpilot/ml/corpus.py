"""Training corpus for the requirement-extraction model.

Why this exists
---------------
The shredder is the highest-volume model stage in the pipeline: it reads the
FULL corpus (invariant 4 — recall beats cost there), in overlapping windows, on
every run. That is where an API-dependent backend costs the most and where a
domain-trained model can genuinely replace it, because the task is narrow and
well-defined: given a sentence from a solicitation, is it a binding
requirement, and of what kind?

Two corpus sources, kept strictly separate because they have different
epistemic status:

  SEED       expert-authored templates over real federal solicitation
             phrasing. Synthetic, and labelled as such. It exists so the
             model has something to learn from before a single run has been
             captured — a cold-start set, not a substitute for real data.

  CAPTURED   sentences from real solicitations processed by real runs, with
             the labels the pipeline actually assigned and a human accepted.
             This is the corpus that matters; it grows every run through the
             MLE loop and should dominate once it exists.

Grouped splitting
-----------------
Every seed example carries a `family`. Templates in one family are
paraphrases of one another, so a random train/test split would put near
duplicates on both sides and report a score the model has not earned. The
trainer splits by FAMILY, never by row. This is the single most important
property of this file: it is what makes the reported metrics mean something.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

# Label space mirrors RequirementCategory, plus the negative class. "none"
# is not a category of requirement — it means "this sentence binds nobody",
# which is most of any solicitation (background, definitions, boilerplate).
CATEGORIES = ("format", "content", "administrative", "evaluation", "none")


@dataclass
class LabeledSentence:
    text: str
    category: str                  # one of CATEGORIES
    family: str                    # grouping key for the split
    source: str = "seed"           # seed | captured
    mandatory: Optional[bool] = None

    @property
    def is_requirement(self) -> bool:
        return self.category != "none"


# --- seed templates ----------------------------------------------------------
# Each entry: (family, category, [phrasings]). Phrasings within a family vary
# the way real solicitations vary — different verbs of obligation, different
# section references, different orderings — so the model learns the pattern
# rather than a fixed string. Slots in {braces} are filled from SLOTS below.

_TEMPLATES: list[tuple[str, str, list[str]]] = [
    # ---- format ------------------------------------------------------------
    ("page-limit", "format", [
        "The offeror shall submit Volume {vol} ({voltitle}) not to exceed {n} pages.",
        "Volume {vol} is limited to {n} pages; pages in excess of the limit will not be evaluated.",
        "{voltitle} proposals exceeding {n} pages will have the excess pages removed and not evaluated.",
        "Do not exceed {n} pages for the {voltitle} volume.",
        "The {voltitle} volume shall be no more than {n} pages in length.",
    ]),
    ("typography", "format", [
        "Text shall be single-spaced, {pt}-point {font}, with one-inch margins.",
        "All narrative shall use {font} {pt} point or larger, with margins of not less than one inch.",
        "Proposals shall be typed in {pt} point {font} font on 8.5 x 11 inch paper.",
        "Font size shall be no smaller than {pt} point ({font}); tables may use {pt2} point.",
    ]),
    ("file-format", "format", [
        "Submit each volume as a separate searchable PDF file.",
        "Files shall be submitted in Adobe PDF format; scanned images are not acceptable.",
        "The pricing volume shall be submitted in Microsoft Excel using the attached template.",
        "Each file shall be named in accordance with the convention specified in Section L.",
    ]),
    ("tabs-structure", "format", [
        "The proposal shall be organized into the tabs identified in Section {sec}.",
        "Offerors shall follow the outline in Section {sec} and shall not reorder the subsections.",
        "The technical volume shall be tabbed to correspond to PWS paragraph numbering.",
    ]),

    # ---- content -----------------------------------------------------------
    ("technical-approach", "content", [
        "The offeror shall describe its technical approach to each task in PWS Section {sec}.",
        "Describe in detail the methodology proposed to accomplish the requirements of PWS {sec}.",
        "The offeror shall explain how it will perform each task identified in the {doc}.",
        "Provide a technical approach addressing every requirement in the Statement of Work.",
    ]),
    ("staffing-plan", "content", [
        "The offeror shall provide a staffing plan identifying labor categories and hours by task.",
        "Submit a staffing approach describing how key personnel will be recruited and retained.",
        "The offeror shall identify all key personnel and provide a resume for each.",
        "Provide a plan for maintaining staffing levels throughout the period of performance.",
    ]),
    ("past-performance", "content", [
        "The offeror shall provide {n} past performance references for contracts of similar size and scope performed within the last {yrs} years.",
        "Submit past performance information on up to {n} recent and relevant contracts.",
        "Offerors shall identify contracts performed within the past {yrs} years that are relevant to this requirement.",
        "Provide contract number, customer point of contact, and dollar value for each reference.",
    ]),
    ("transition-plan", "content", [
        "The offeror shall submit a transition plan covering the {n}-day phase-in period.",
        "Describe the approach to assuming full performance without degradation of service.",
        "The transition plan shall address knowledge transfer, staffing, and risk mitigation.",
    ]),
    ("quality-plan", "content", [
        "The offeror shall submit a Quality Control Plan describing inspection and corrective action procedures.",
        "Provide a quality management approach consistent with the Performance Requirements Summary.",
        "The offeror shall describe its process for identifying and correcting deficient performance.",
    ]),
    ("pricing-content", "content", [
        "The offeror shall complete the pricing template for the base period and all option periods.",
        "Provide fully burdened hourly rates by labor category for each period of performance.",
        "The cost volume shall include a basis of estimate supporting all proposed hours.",
        "Offerors shall submit supporting documentation for all proposed indirect rates.",
    ]),
    ("compensation-plan", "content", [
        "The offeror shall submit a Total Compensation Plan for professional employees in accordance with FAR 52.222-46.",
        "Provide salary ranges and fringe benefits for all professional labor categories proposed.",
        "The compensation plan shall be supported by recognized regional survey data.",
    ]),

    # ---- administrative ----------------------------------------------------
    ("submission-deadline", "administrative", [
        "Proposals shall be submitted via email to the Contract Specialist no later than {time} on the closing date.",
        "Offers are due not later than {time} on {date}; late offers will be handled in accordance with FAR 52.215-1.",
        "Submit all volumes to {email} by {time} local time.",
        "Responses received after {time} will not be considered.",
    ]),
    ("portal-submission", "administrative", [
        "Proposals shall be submitted through the {portal} portal; email submissions will not be accepted.",
        "Offerors shall register in {portal} prior to submission.",
        "All documents shall be uploaded to {portal}; the Government is not responsible for upload failures.",
    ]),
    ("reps-and-certs", "administrative", [
        "The offeror shall complete the representation at FAR {clause} regarding covered telecommunications equipment.",
        "Offerors shall complete all representations and certifications in Section K.",
        "The offeror shall have an active registration in SAM.gov at the time of proposal submission.",
        "Complete and return the certification at FAR {clause} with the offer.",
    ]),
    ("questions-deadline", "administrative", [
        "Questions concerning this solicitation shall be submitted in writing no later than {date}.",
        "All questions must be received by {time} on {date}; questions received after that time may not be answered.",
        "Submit questions to the Contracting Officer at {email}.",
    ]),
    ("validity-period", "administrative", [
        "Offers shall remain valid for {n} calendar days from the date of submission.",
        "The offeror agrees to hold its proposal open for acceptance for {n} days.",
    ]),

    # ---- evaluation --------------------------------------------------------
    ("eval-technical", "evaluation", [
        "The Government will evaluate the soundness and feasibility of the offeror's technical approach.",
        "The technical factor will be evaluated for the extent to which the proposed approach demonstrates understanding of the requirement.",
        "Proposals will be assessed on the realism of the proposed methodology.",
    ]),
    ("eval-past-perf", "evaluation", [
        "Past performance will be evaluated for recency, relevancy, and quality.",
        "The Government will assess the offeror's record of performance on similar efforts.",
        "An offeror with no relevant past performance will receive a neutral rating.",
    ]),
    ("eval-price", "evaluation", [
        "The Government will evaluate price for reasonableness and completeness.",
        "Price will be evaluated using the total evaluated price for the base and all option periods.",
        "Unbalanced pricing may be grounds for rejection of the offer.",
    ]),
    ("eval-tradeoff", "evaluation", [
        "Award will be made to the offeror whose proposal represents the best value to the Government.",
        "Technical factors, when combined, are significantly more important than price.",
        "The Government intends to award without discussions; offerors should submit their best terms initially.",
    ]),

    # ---- none: the majority class in any real solicitation ------------------
    ("background", "none", [
        "The {agency} operates a nationwide network of field offices supporting mission delivery.",
        "This requirement supports the {agency} enterprise infrastructure program.",
        "The incumbent contract number is {contract}.",
        "The current contract expires on {date}.",
        "This is a follow-on to contract {contract}.",
    ]),
    ("definitions", "none", [
        "For the purposes of this document, 'Government' means the {agency}.",
        "'Deliverable' means any item identified in the Contract Data Requirements List.",
        "The terms used herein have the meanings assigned in FAR Part 2.",
        "'PWS' means Performance Work Statement.",
    ]),
    ("boilerplate", "none", [
        "This solicitation is issued as a Request for Proposal under FAR Part 15.",
        "The North American Industry Classification System code for this acquisition is {naics}.",
        "The Product Service Code is {psc}.",
        "This acquisition is set aside for small business concerns.",
        "The period of performance is one base year plus {n} option years.",
    ]),
    ("clause-incorporation", "none", [
        "The following clauses are incorporated by reference: FAR {clause}.",
        "FAR {clause} applies to this acquisition.",
        "Clause FAR {clause} is incorporated by reference with the same force and effect as if set forth in full text.",
        "The full text of the referenced clauses may be found at acquisition.gov.",
    ]),
    ("narrative-noise", "none", [
        "Table {n} summarizes the historical workload volumes.",
        "See Attachment {n} for the wage determination.",
        "Figure {n} depicts the current system architecture.",
        "The Government anticipates awarding a single contract.",
        "A site visit will be held; attendance is optional.",
    ]),

    # ---- format (continued) -------------------------------------------------
    ("copies-binding", "format", [
        "Submit one (1) original and {n} copies of each volume.",
        "The offeror shall provide {n} hard copies and one electronic copy on removable media.",
        "Each volume shall be separately bound and separately paginated.",
        "Volumes shall be submitted as separate files and shall not be combined.",
    ]),
    ("page-counting-rules", "format", [
        "Cover pages, tables of contents, and dividers do not count toward the page limit.",
        "Resumes are excluded from the {n}-page limitation.",
        "A page is defined as one side of a sheet of 8.5 x 11 inch paper.",
        "Pages containing only graphics count toward the page limitation.",
        "Foldouts count as {n} pages each.",
    ]),
    ("file-naming", "format", [
        "Files shall be named using the convention: OfferorName_VolumeNumber_SolicitationNumber.",
        "Each electronic file shall include the offeror name and volume title in the file name.",
        "File names shall not exceed {n} characters and shall not contain special characters.",
    ]),
    ("no-cross-reference", "format", [
        "Offerors shall not incorporate material by reference from other volumes.",
        "Each volume shall stand alone; cross-references to other volumes will not be evaluated.",
        "Information required in the technical volume shall not be placed in an appendix.",
    ]),
    ("graphics-tables", "format", [
        "Tables and figures shall be legible at 100 percent magnification.",
        "Graphics shall use font no smaller than {pt2} point.",
        "All tables shall be numbered and titled.",
    ]),
    ("headers-footers", "format", [
        "Each page shall bear the solicitation number in the header.",
        "Pages shall be numbered consecutively within each volume.",
        "The offeror name shall appear in the footer of every page.",
    ]),
    ("media-delivery", "format", [
        "Electronic media shall be virus-scanned prior to submission.",
        "Removable media shall be labeled with the offeror name and solicitation number.",
        "Individual file size shall not exceed {n} megabytes.",
    ]),

    # ---- content (continued) ------------------------------------------------
    ("risk-management", "content", [
        "The offeror shall identify the principal risks to performance and its mitigation approach.",
        "Describe risks associated with transition and how they will be controlled.",
        "Provide a risk register addressing technical, schedule, and staffing risk.",
    ]),
    ("subcontracting-plan", "content", [
        "Large business offerors shall submit a Small Business Subcontracting Plan in accordance with FAR 19.704.",
        "The offeror shall identify all proposed subcontractors and the work each will perform.",
        "Provide the percentage of work to be performed by the prime contractor.",
    ]),
    ("security-approach", "content", [
        "The offeror shall describe its approach to obtaining and maintaining required personnel security clearances.",
        "Describe the approach to safeguarding covered defense information in accordance with DFARS 252.204-7012.",
        "The offeror shall describe its system security plan and incident reporting process.",
    ]),
    ("key-personnel", "content", [
        "The offeror shall submit letters of commitment for all proposed key personnel.",
        "Resumes shall demonstrate that each key person meets the minimum qualifications in the {doc}.",
        "Identify the proposed Program Manager and describe their authority.",
    ]),
    ("management-approach", "content", [
        "The offeror shall describe its management structure and lines of authority.",
        "Describe the approach to communication and reporting with the Contracting Officer's Representative.",
        "Provide an organizational chart showing the proposed contract organization.",
    ]),
    ("small-business-participation", "content", [
        "The offeror shall describe the extent of participation by small business concerns.",
        "Provide the proposed percentage of contract value to be performed by small businesses.",
    ]),

    # ---- administrative (continued) -----------------------------------------
    ("amendment-acknowledgment", "administrative", [
        "Offerors shall acknowledge receipt of all amendments to this solicitation.",
        "Failure to acknowledge amendments may render the offer unacceptable.",
        "Acknowledge amendments by completing Block {n} of the SF 1449.",
    ]),
    ("poc-identification", "administrative", [
        "The offeror shall identify a single point of contact for proposal clarification.",
        "Provide the name, title, telephone number, and email address of the authorized negotiator.",
        "The offeror shall provide its CAGE code and Unique Entity Identifier.",
    ]),
    ("cover-letter", "administrative", [
        "The proposal shall include a transmittal letter signed by an official authorized to bind the offeror.",
        "The transmittal letter shall identify the authorized negotiator for the offeror.",
        "The cover letter shall be signed by a corporate officer.",
    ]),
    ("site-visit", "administrative", [
        "Offerors intending to attend the site visit shall register by {date}.",
        "Attendance at the pre-proposal conference requires registration no later than {time} on {date}.",
        "Offerors shall submit the names of attendees for badging at least {n} days in advance.",
    ]),
    ("oral-presentation", "administrative", [
        "Offerors shall be prepared to deliver an oral presentation within {n} days of notification.",
        "Oral presentation slides shall be submitted {n} days prior to the presentation.",
        "The Government will schedule presentations in the order proposals were received.",
    ]),
    ("proposal-withdrawal", "administrative", [
        "Proposals may be withdrawn by written notice received prior to the time set for receipt.",
        "Modifications to proposals shall be submitted in the same manner as the original.",
    ]),

    # ---- evaluation (continued) ---------------------------------------------
    ("eval-subfactors", "evaluation", [
        "The technical factor consists of {n} subfactors of equal importance.",
        "Subfactor {n} is more important than the remaining subfactors combined.",
        "All evaluation factors other than price, when combined, are approximately equal to price.",
    ]),
    ("eval-ratings", "evaluation", [
        "Each technical subfactor will be assigned an adjectival rating of Outstanding, Good, Acceptable, Marginal, or Unacceptable.",
        "Proposals rated Unacceptable in any technical subfactor will be ineligible for award.",
        "A confidence assessment will be assigned for past performance.",
    ]),
    ("eval-risk", "evaluation", [
        "The Government will assess the risk associated with the offeror's proposed approach.",
        "Weaknesses and significant weaknesses will be documented in the evaluation record.",
        "Proposals containing a deficiency will not be considered for award.",
    ]),
    ("eval-compensation-realism", "evaluation", [
        "The Government will evaluate the realism of proposed professional employee compensation.",
        "Proposed compensation that is unrealistically low may be viewed as reflecting a lack of understanding of the requirement.",
        "The Government will evaluate the total compensation plan for its ability to attract and retain qualified personnel.",
    ]),
    ("eval-responsibility", "evaluation", [
        "Award will be made only to a responsible offeror as defined in FAR 9.104-1.",
        "The Government may request additional information to make a responsibility determination.",
    ]),

    # ---- none (continued) ---------------------------------------------------
    ("gfp-gfe", "none", [
        "The Government will provide office space and network access at the {agency} facility.",
        "Government-furnished equipment is listed in Attachment {n}.",
        "The contractor shall not be provided parking at Government facilities.",
    ]),
    ("pop-place", "none", [
        "The place of performance is the {agency} headquarters and contractor facilities.",
        "Work will be performed primarily at the contractor's location.",
        "The period of performance consists of a {n}-month base period.",
    ]),
    ("contact-listing", "none", [
        "The Contracting Officer for this acquisition is identified in Block {n}.",
        "The Contracting Officer's Representative will be designated at award.",
        "Questions may be directed to the individuals listed above.",
    ]),
    ("history-context", "none", [
        "The {agency} has operated this program since {n}.",
        "Historical workload data is provided for informational purposes only.",
        "The Government makes no guarantee regarding future workload volumes.",
        "This information is provided to assist offerors in preparing their proposals.",
    ]),

    # ---- format: EXTENT limits -------------------------------------------
    # Format requirements come in two flavors, and the first version of this
    # corpus only covered one. Everything above constrains presentational
    # mechanics (fonts, files, naming, tabs); these constrain how MUCH may be
    # submitted. Holding out the extent families removed the whole flavor and
    # the model read page limits as content or evaluation.
    ("resume-limits", "format", [
        "Resumes shall not exceed {n} pages per individual.",
        "Each resume is limited to {n} pages, single-sided.",
        "Key personnel resumes are limited to {n} pages and are excluded from the volume page count.",
    ]),
    ("appendix-limits", "format", [
        "Appendices shall not exceed {n} pages in total.",
        "Attachments are limited to {n} pages and shall contain no narrative.",
        "The appendix may not be used to extend the technical narrative.",
    ]),
    ("slide-limits", "format", [
        "Oral presentation slides shall not exceed {n} slides.",
        "The briefing shall be limited to {n} charts, excluding the title slide.",
        "Slides shall be submitted in PowerPoint and shall not exceed {n} pages when printed.",
        "The oral presentation shall not exceed {n} minutes.",
    ]),
    ("word-limits", "format", [
        "The executive summary shall not exceed {n} words.",
        "Responses to each question are limited to {n} words.",
        "Each past performance narrative shall not exceed {n} words.",
    ]),
    ("section-length", "format", [
        "Section {sec} of the technical volume shall not exceed {n} pages.",
        "No single subfactor response may exceed {n} pages.",
        "The management approach is limited to {n} pages within the {n} page volume.",
    ]),
    ("submission-size", "format", [
        "The complete submission shall not exceed {n} megabytes.",
        "Total upload size is limited to {n} MB across all files.",
        "Offerors shall split submissions exceeding {n} megabytes into multiple uploads.",
    ]),
    ("exhibit-limits", "format", [
        "Exhibits are limited to {n} per volume.",
        "The offeror may include no more than {n} figures in the technical volume.",
        "Tables in excess of {n} will not be evaluated.",
    ]),

    # ---- evaluation: broader coverage ---------------------------------------
    # This was the thinnest class in the corpus and its metric swung hardest
    # when families were held out. Evaluation language is its own register:
    # the GOVERNMENT is the actor, and the verbs are assess, evaluate,
    # consider, rate -- not "shall submit".
    ("eval-basis-of-award", "evaluation", [
        "Award will be made on a lowest price technically acceptable basis.",
        "The Government will award to the responsible offeror whose proposal is most advantageous.",
        "This is a best value tradeoff acquisition under FAR 15.101-1.",
        "The Government reserves the right to award without discussions.",
    ]),
    ("eval-technical-acceptability", "evaluation", [
        "Proposals will be evaluated for compliance with the requirements of Section {sec}.",
        "A proposal that fails to meet a material requirement will be rated unacceptable.",
        "The Government will determine whether the proposed approach is technically acceptable.",
    ]),
    ("eval-price-realism", "evaluation", [
        "The Government will perform a price realism analysis on proposed labor rates.",
        "Proposed costs will be evaluated for realism in relation to the technical approach.",
        "A price that is unrealistically low may indicate a lack of understanding of the requirement.",
    ]),
    ("eval-relevancy", "evaluation", [
        "Relevancy will be assessed based on similarity of scope, magnitude, and complexity.",
        "The Government will consider contracts of similar dollar value in assessing relevancy.",
        "More relevant past performance will be given greater weight.",
    ]),
    ("eval-key-personnel", "evaluation", [
        "The Government will evaluate whether proposed key personnel meet the stated qualifications.",
        "Resumes will be assessed for depth of relevant experience.",
        "The Government will consider the availability and commitment of proposed staff.",
    ]),
    ("eval-transition", "evaluation", [
        "The Government will evaluate the feasibility of the proposed transition approach.",
        "Transition risk will be assessed as part of the management factor.",
        "The Government will consider the offeror's plan for uninterrupted service.",
    ]),
    ("eval-clarifications", "evaluation", [
        "The Government may seek clarifications without opening discussions.",
        "If discussions are held, offerors in the competitive range will receive evaluation notices.",
        "The Government may establish a competitive range in accordance with FAR 15.306.",
    ]),

    # ---- none: more of the majority class -----------------------------------
    ("scope-narrative", "none", [
        "The contractor will support approximately {n} users across the enterprise.",
        "The environment consists of {n} servers and associated network infrastructure.",
        "Service levels are described in the Performance Requirements Summary.",
        "Workload has grown approximately {n} percent annually.",
    ]),
    ("admin-context", "none", [
        "This solicitation is issued under the authority of FAR Part 12.",
        "The resulting contract will be a firm fixed price contract.",
        "The Government contemplates award of a single indefinite delivery contract.",
        "Option periods may be exercised at the sole discretion of the Government.",
    ]),
    ("attachment-listing", "none", [
        "Attachment {n} contains the Performance Work Statement.",
        "The wage determination is provided as Attachment {n}.",
        "A list of Government furnished equipment appears in Attachment {n}.",
        "The pricing template is provided as a separate Excel workbook.",
    ]),
    ("dates-informational", "none", [
        "The anticipated award date is {date}.",
        "The Government expects to complete evaluations by {date}.",
        "The estimated start of performance is {date}.",
    ]),

    # ---- content: more coverage ---------------------------------------------
    ("phase-in-staffing", "content", [
        "The offeror shall describe how it will achieve full staffing by the end of phase-in.",
        "Describe the approach to hiring incumbent personnel, if any.",
        "Provide the proposed staffing ramp by month during transition.",
    ]),
    ("reporting-deliverables", "content", [
        "The offeror shall describe its approach to producing the deliverables in the CDRL.",
        "Describe the proposed monthly status report content and format.",
        "The offeror shall identify the tools used to track and report performance metrics.",
    ]),
    ("surge-support", "content", [
        "The offeror shall describe its ability to provide surge support on short notice.",
        "Describe the approach to scaling staff for periods of increased demand.",
        "The offeror shall explain how surge requests will be staffed within {n} days.",
    ]),
    ("innovation-approach", "content", [
        "The offeror shall describe any process improvements it proposes to introduce.",
        "Describe proposed automation that will reduce manual effort over the period of performance.",
    ]),

    # ---- administrative: more coverage --------------------------------------
    ("small-business-rep", "administrative", [
        "The offeror shall represent its size status under NAICS {naics}.",
        "Offerors shall complete the small business representation in Section K.",
        "The offeror shall certify that it qualifies as a small business concern.",
    ]),
    ("conflict-of-interest", "administrative", [
        "The offeror shall disclose any actual or potential organizational conflict of interest.",
        "Offerors shall submit an OCI mitigation plan if a conflict is identified.",
    ]),
    ("insurance-bonding", "administrative", [
        "The offeror shall provide evidence of insurance prior to award.",
        "The successful offeror shall furnish a performance bond within {n} days of award.",
        "Certificates of insurance shall name the Government as an interested party.",
    ]),

    # ---- boundary coverage --------------------------------------------------
    # The leave-one-family-out audit showed the residual errors are not spread
    # evenly: they concentrate on format<->administrative and
    # content<->administrative, where the verb is identical ("shall submit")
    # and only the OBJECT distinguishes them. These families sit deliberately
    # on those boundaries so the model sees the distinction more than once.
    ("volume-order", "format", [
        "Volumes shall be submitted in the order listed in Section {sec}.",
        "The table of contents shall list every subfactor heading.",
        "Each volume shall begin with a table of contents.",
        "Section headings shall match the numbering used in Section {sec}.",
    ]),
    ("cross-reference-index", "format", [
        "The offeror shall provide a compliance matrix cross-referencing each requirement to a page number.",
        "A cross-reference index shall map Section L instructions to proposal locations.",
        "Page references in the compliance matrix shall be exact.",
    ]),
    ("assembly-instructions", "format", [
        "Do not include marketing material in any volume.",
        "The technical volume shall contain no pricing information.",
        "Pricing shall appear only in the price volume.",
        "Classified information shall not be included in the proposal.",
    ]),
    ("debriefing-request", "administrative", [
        "Requests for debriefing shall be submitted in writing within {n} days of notification.",
        "Unsuccessful offerors may request a debriefing in accordance with FAR 15.506.",
        "Debriefing requests shall be addressed to the Contracting Officer.",
    ]),
    ("questionnaire-distribution", "administrative", [
        "The offeror shall send the past performance questionnaire directly to each reference.",
        "References shall return questionnaires to the Contracting Officer, not to the offeror.",
        "Questionnaires received after {date} may not be considered.",
    ]),
    ("authorized-negotiator", "administrative", [
        "The offeror shall identify individuals authorized to negotiate on its behalf.",
        "Provide the name and telephone number of the person authorized to sign the contract.",
    ]),
    ("corporate-experience", "content", [
        "The offeror shall describe its corporate experience performing work of similar scope.",
        "Describe the offeror's experience supporting federal customers in this mission area.",
        "The offeror shall demonstrate understanding of the operating environment.",
    ]),
    ("quality-metrics", "content", [
        "The offeror shall propose performance metrics for each service area.",
        "Describe how service level attainment will be measured and reported.",
        "The offeror shall explain its approach to root cause analysis of missed metrics.",
    ]),
    ("retention-approach", "content", [
        "The offeror shall describe its approach to retaining qualified personnel.",
        "Describe compensation and career development practices that support retention.",
        "Explain how turnover will be minimized during the period of performance.",
    ]),
]



SLOTS: dict[str, list[str]] = {
    "vol": ["I", "II", "III", "IV"],
    "voltitle": ["Technical", "Management", "Past Performance", "Price", "Cost/Price"],
    "n": ["3", "5", "10", "15", "20", "25", "30", "45", "60", "90", "120"],
    "yrs": ["three (3)", "five (5)", "3", "5"],
    "pt": ["10", "11", "12"],
    "pt2": ["8", "9", "10"],
    "font": ["Times New Roman", "Arial", "Calibri"],
    "sec": ["3", "3.2", "C.5", "L.4", "M.2", "5.1"],
    "doc": ["Performance Work Statement", "Statement of Work", "Statement of Objectives"],
    "time": ["2:00 PM Eastern Time", "12:00 PM ET", "5:00 p.m. Eastern", "10:00 AM EST"],
    "date": ["March 14, 2027", "August 15, 2026", "2027-03-14", "14 March 2027"],
    "email": ["contracting@agency.gov", "co.specialist@agency.gov"],
    "portal": ["PIEE", "GSA eBuy", "FedConnect", "Unison Marketplace", "SAM.gov"],
    "clause": ["52.204-24", "52.204-26", "52.209-5", "52.219-6", "52.222-46", "52.237-10"],
    "agency": ["General Services Administration", "Department of Veterans Affairs",
               "Department of the Army", "Department of Homeland Security"],
    "contract": ["GS-35F-0119Y", "W912DY-21-C-0034", "36C10X22D0001"],
    "naics": ["541511", "541512", "541519", "561210"],
    "psc": ["D307", "D399", "R499"],
}


def _fill(template: str, rng: random.Random) -> str:
    out = template
    for slot, options in SLOTS.items():
        token = "{" + slot + "}"
        while token in out:
            out = out.replace(token, rng.choice(options), 1)
    return out


def seed_corpus(per_phrasing: int = 6, seed: int = 17) -> list[LabeledSentence]:
    """Expert-authored cold-start set. Deterministic for a given seed."""
    rng = random.Random(seed)
    rows: list[LabeledSentence] = []
    for family, category, phrasings in _TEMPLATES:
        for phrasing in phrasings:
            seen: set[str] = set()
            for _ in range(per_phrasing):
                text = _fill(phrasing, rng)
                if text in seen:
                    continue          # slot filling can collide; keep it unique
                seen.add(text)
                rows.append(LabeledSentence(
                    text=text, category=category, family=family, source="seed",
                    mandatory=_mandatory_hint(text),
                ))
    return rows


# "shall" / "must" / "will be required" bind; "may" / "should" / "intends" do
# not. This is a labelling heuristic for the seed set only — the model learns
# from the text, and captured examples carry whatever the pipeline recorded.
_BINDING = (" shall ", " shall,", " must ", "are due", "is limited to",
            "do not exceed", "will not be evaluated", "will not be considered",
            "are required", "is required")


def _mandatory_hint(text: str) -> bool:
    lowered = f" {text.lower()} "
    return any(token in lowered for token in _BINDING)


# --- captured corpus ---------------------------------------------------------


def captured_corpus(output_root: Path) -> list[LabeledSentence]:
    """Real requirement text from completed runs.

    Every accepted requirement in a run's compliance matrix is a positive
    example with the category the pipeline assigned. These outrank the seed
    set: they are real solicitation language a human let through a gate.
    """
    rows: list[LabeledSentence] = []
    if not output_root.is_dir():
        return rows
    for run_dir in sorted(p for p in output_root.iterdir() if p.is_dir()):
        state_file = run_dir / "state.json"
        if not state_file.exists():
            continue
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        matrix = state.get("matrix") or {}
        for req in matrix.get("requirements") or []:
            text = (req.get("verbatim_text") or "").strip()
            category = (req.get("category") or "").strip()
            if len(text) < 15 or category not in CATEGORIES:
                continue
            rows.append(LabeledSentence(
                text=text, category=category,
                family=f"run:{run_dir.name}",     # never split a run across sides
                source="captured",
            ))
    return rows


def build_corpus(output_root: Optional[Path] = None,
                 per_phrasing: int = 6,
                 seed: int = 17) -> list[LabeledSentence]:
    rows = seed_corpus(per_phrasing=per_phrasing, seed=seed)
    if output_root is not None:
        rows += captured_corpus(output_root)
    return rows


def families(rows: list[LabeledSentence]) -> list[str]:
    return sorted({row.family for row in rows})


def iter_jsonl(rows: list[LabeledSentence]) -> Iterator[str]:
    for row in rows:
        yield json.dumps({
            "text": row.text, "category": row.category, "family": row.family,
            "source": row.source, "mandatory": row.mandatory,
        })


@dataclass
class CorpusStats:
    total: int = 0
    by_category: dict = field(default_factory=dict)
    by_source: dict = field(default_factory=dict)
    families: int = 0

    @classmethod
    def of(cls, rows: list[LabeledSentence]) -> "CorpusStats":
        stats = cls(total=len(rows), families=len(families(rows)))
        for row in rows:
            stats.by_category[row.category] = stats.by_category.get(row.category, 0) + 1
            stats.by_source[row.source] = stats.by_source.get(row.source, 0) + 1
        return stats
