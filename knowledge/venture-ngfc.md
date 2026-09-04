# NGFC — pilot notes

## Strategy (agreed with Stan [2026-08-21])
- Green Collar's role: integrator, not manufacturer.
- **Phase 1**: deploy 2–3 evaluation fuel-cell units on Green Collar's own wells — one sweet, pipeline-quality control site, and one sour well behind a self-built gas-conditioning skid. Collect 6+ months of data.
- **Phase 2**: productize the conditioning skid (desulfurization + fuel cell + telemetry + service offering).
- **Phase 3**: offer power-as-a-service to Ohio oil/gas operators (~28 candidate operators mapped in the WellCollar research project), aiming to capture the 30% Investment Tax Credit under IRC §48E.
- **Kill criteria** (reasons to abandon the pilot): field-gas causes fuel-cell degradation more than 2× per year versus pipeline-quality gas even after cleanup treatment; or the 5-year total cost fails to beat a Qnergy Stirling generator alternative.

## Supplier outreach — sent 2026-08-21 from stan@greencollarindustries.com
| Vendor | To | Product | Gmail msg id |
|---|---|---|---|
| WATT Fuel Cell (Mt Pleasant, PA) | danielle.ramaley@wattfuelcell.com (CCO), cc info@ | WATT HOME 2kW SOFC / WATT REMOTE (RedHawk channel) | 1a025ac0497a663d |
| Upstart Power (Southborough, MA) | info@upstartpower.com | Upgen NXG SOFC, natural gas/propane, modular | 1a025ac2886dd1fd |
| ATX Networks | sales@atx.com | Areca HPS SOFC hybrid, natural gas/propane, AC/DC | 1a025ac47bc30a0f |

Each vendor was asked about: eval-unit pricing and lead time, inlet gas spec (sulfur ppb tolerance), desulfurizer service intervals/costs, and commissioning requirements. Outreach was framed strictly as a buyer inquiry — no partnership discussion.

**Benchmark option (not yet contacted):** Qnergy PowerGen Stirling engine (Ogden, UT) — the incumbent off-the-shelf solution used for cathodic-protection/SCADA power in the oil-field industry; serves as the cost/performance comparison point for the kill criteria.

## Open next steps
- Stan to pick 2–3 candidate well sites (one sour, one sweet).
- Map DOE SBIR Phase I and Ohio Third Frontier deadlines.
- Build a pilot economics model: unit + cleanup cost, ITC treatment, versus the current site's power cost.
- Watch the Green Collar Industries inbox for vendor replies.

## Funding map (added 2026-08-21)
Reference PDF: `projects/ngfc/funding_map_2026-08-21.pdf` — verified 2026-08-21.

Key facts:
- SBIR program lapsed October 2025, reauthorized 2026-04-13 via P.L. 119-83.
- DOE FECM/NG (fossil energy/carbon management, natural gas) topics fall under Release 2 — watch for fall 2026 announcement.
- TVSF (Third Frontier / TechGROWTH-style) Phase 2 funding: up to $200k for licensing Ohio-institution technology — potential fit with Chuang / University of Akron research (see publication list below). Next funding round approximately October–November 2026, with only a ~2-week application window.
- JobsOhio Energy Opportunity Initiative: $100M fund for natural-gas infrastructure/power generation projects, rolling applications, accessed via APEG.
- Ohio Advanced Energy Fund: ruled out — it's efficiency-only and requires a utility-usage-reduction basis, which doesn't fit this project.

Next steps: complete SAM.gov / SBA / PAMS registration; schedule September intro calls with University of Akron tech transfer office and APEG.

## SAM.gov registration support (2026-08-21)
Stan is registering **Green Collar Energy, LLC** on SAM.gov.

Documents delivered: `projects/ngfc/sam/` containing:
- `sam_gov_crib_sheet.pdf`
- `GreenCollarEnergy_LLC_Articles_OhioSoS.pdf`
- `GreenCollarIndustries_Inc_Articles_OhioSoS.pdf`

Ohio Secretary of State verification [ohiosos, 2026-08-21]:
- **Green Collar Energy, LLC** — entity #5040748, formed 04/26/2023, registered address 995 Kensington Rd, Coshocton, OH 43812, stated purpose "generating power for profit", registered agent = Green Collar Industries, Inc.
- **Green Collar Industries Inc.** — entity #4957693, a holding corporation, registered agent = Golden City Capital Trust.
- **Green Collar Oil Field Services LLC** — entity #4957979.

**Entity decision:** Green Collar Energy, LLC (GCE) is the entity registering for SAM/SBIR/TVSF and will be the operating company that signs vendor purchase orders.

Pending: after Stan submits the SAM registration, expect 2–4 weeks validation time, then proceed to sbir.gov and PAMS registration. An annual SAM renewal reminder should be offered/set up.

**Gotcha:** the Ohio SoS business-search site blocks datacenter IPs (returns "bad web browser credentials" error) — use a browser with `proxy=True` to work around this. Filing PDFs are retrieved via `bizimage.ohiosos.gov/api/image/pdf/{docid}` plus the download_files mechanism.

- **2026-08-24**: Stan submitted SAM entity-validation documents, reference **INC-GSAFSD21548006**, ETA 1.5–3.5 business days (expected ~Wed–Thu 8/26–27). Remaining SAM sections (Taxpayer Info onward) are locked until validation passes. [sam.gov, 2026-08-24]

## Chuang fuel-cell publications (Steven S.C. Chuang, University of Akron)
Reference list of relevant published research (potential technology-licensing fit for TVSF Phase 2, per funding map above). Numbers are the source bibliography's item numbers.

- **190.** "Electrocatalytic Methane Oxidation to Ethanol on Iron-Nickel Hydroxide Nanosheets" — Li, J., Yao, L., Wu, D., King, J., Chuang, S.S., Liu, B., Peng, Z. *Applied Catalysis B: Environmental*, 2022, 303, 120890. doi:10.1016/j.apcatb.2022.121657
- **165.** "CH4 internal dry reforming over a Ni/YSZ/ScSZ anode catalyst in a SOFC: A transient kinetic study" — Yin, W., Chuang, S.S.C. *Catal Commun*, 2017, 102 (Suppl. C), 62-66. doi:10.1016/j.catcom.2017.08.027
- **159.** "Diffusion-limited Electrochemical Oxidation of H2/CO on Ni-anode Catalyst in a CH4/CO2-Solid Oxide Fuel Cell" — Modjtahedi, A., Hedayat, N., Chuang, S.S.C. *Catalysis Today*, 2016, 178, Part 2, 227-236. doi:10.1016/j.cattod.2015.12.026
- **155.** "Perovskites and Related Mixed Oxides for SOFC Applications" — Chuang, S.S.C., Zhang, L. in *Perovskites and Related Mixed Oxides*, Wiley-VCH Verlag GmbH & Co. KGaA, 2016, pp. 863-880. doi:10.1002/9783527686605.ch38
- **149.** "Electroless Plated Cu-Ni Anode Catalyst for Natural Gas Solid Oxide Fuel Cells" — Rismanchian, A., Mirzababaei, J., Chuang, S.S.C. *Catalysis Today*, 2015, 245, 79-85. doi:10.1016/j.cattod.2014.05.012
- **148.** "Solid Oxide Fuel Cells Fueled with Reduced Fe/Ti Oxide" — Mirzababaei, J., Fan, L.S., Chuang, S.S.C. *Journal of Materials Chemistry A*, 2015, 3(5), 2242-2250. doi:10.1039/c4ta04905e
- **144.** "Enhanced Performance of Polymer Solar Cells using PEDOT:PSS Doped with Fe3O4 Magnetic Nanoparticles Aligned by an External Magnetostatic Field as an Anode Buffer Layer" — Wang, K. et al. (incl. Chuang, S.S.C.) *ACS Applied Materials & Interfaces*, 2014, 6(15), 13201-13208. doi:10.1021/am503041g
- **139.** "The Direct Carbon Solid Oxide Fuel Cell with H2 and H2O Feeds" — Modjtahedi, A., Hedayat, N., Chuang, S.S.C. *Solid State Ionics*, 2014, 268, 15-22. doi:10.1016/j.ssi.2014.09.014
- **138.** "La0.6Sr0.4Co0.2Fe0.8O3 Perovskite: A Stable Anode Catalyst for Direct Methane Solid Oxide Fuel Cells" — Mirzababaei, J., Chuang, S.S.C. *Catalysts*, 2014, 4(2), 146-161. doi:10.3390/catal4020146
- **129.** "Analysis of Gas Products from Direct Utilization of Carbon in a Solid Oxide Fuel Cell" — Siengchum, T., Guzman, F., Chuang, S.S.C. *Journal of Power Sources*, 2012, 213, 375-381. doi:10.1016/j.jpowsour.2012.04.020
- **127.** "Direct Use of Sulfur-Containing Coke on a Ni-Yttria-Stabilized Zirconia Anode Solid Oxide Fuel Cell" — Guzman, F., Singh, R., Chuang, S.S.C. *Energy & Fuels*, 2011, 25(5), 2179-2186. doi:10.1021/ef1016363
- **126.** "Investigation of Boudouard Reactions on Carbon-based Solid Oxide Fuel Cells by Transient Techniques" — Chien, A.C., Siengchum, T., Chuang, S.S.C. *Solid State Topics (General) – 218th ECS Meeting*, 2011, 33(31), 75-85. doi:10.1149/1.3567405
- **125.** "Effect of Gas Flow Rates and Boudouard Reactions on the Performance of Ni/YSZ Anode Supported Solid Oxide Fuel Cells with Solid Carbon Fuels" — Chien, A.C., Chuang, S.S.C. *Journal of Power Sources*, 2011, 196(10), 4719-4723. doi:10.1016/j.jpowsour.2011.01.033
- **118.** "Pulse CH4/D2O Reaction on a Ni/YSZ Anode in SOFC" — Yu, Z.Q., Chuang, S.S.C. *Applied Catalysis A-General*, 2007, 327(2), 147-156. doi:10.1016/j.apacata.2007.05.008
- **112.** "Investigating the CH4 Reaction Pathway on a Novel LSCF Anode Catalyst in the SOFC" — Fisher, J.C., Chuang, S.S.C. *Catalysis Communications*, 2009, 10(6), 772-776. doi:10.1016/j.catcom.2008.11.035
- **104.** "Performance and Byproduct Analysis of Coal Gas Solid Oxide Fuel Cell" — Singh, R., Guzman, F., Khatri, R., Chuang, S.S.C. *Energy & Fuels*, 2010, 24(2), 1176-1183. doi:10.1021/ef9009636
- **97.** "Catalysis of Solid Oxide Fuel Cells" — Chuang, S.S.C. in *Catalysis: Volume 18*, J.J. Spivey (Ed.), The Royal Society of Chemistry, 2005, pp. 186-198. doi:10.1039/9781847553300-00186
- **94.** "In-Situ IR Study of Transient CO2 Reforming of CH4 Over Rh/Al2O3" — Stevens, R.W., Chuang, S.S.C. *Journal of Physical Chemistry B*, 2004, 108(2), 696-703. doi:10.1021/jp0367530
- **77.** "Oxidative Carbonylation of Methanol to Dimethylcarbonate over Copper Complex Catalysts" — Wang, G., Huang, T., Lin, M., Chuang, S.S.C. *Journal of Natural Gas*, 2000, 9(1), 8-17.
- **17.** "The Effect of Adsorbed Sulfur on Heterogeneous Hydroformylation over Rh, Ni, and Ru Catalysts" — Balakos, M.W., Pien, S.I., Chuang, S.S.C. in *Studies in Surface Science and Catalysis, Catalyst Deactivation-1991*, H.B. Calvin & B.B. John (Eds.), Elsevier, 1991, pp. 549-556. doi:10.1016/S0167-2991(08)62682-8
- **13.** "Enhancement of Ethylene Hydroformylation over Ni SiO2 through Sulfur Promotion" — Chuang, S.S.C., Pien, S.I. *Catalysis Letters*, 1990, 6(3-6), 389-394. doi:10.1007/Bf00764006
