# Three-minute demonstration

Every number below is read from the generated result files at build time. Say the caveats out loud; they are on screen too.

**0:00–0:30 — Problem.** Neighbourhoods are getting solar, EVs and batteries, and still losing power. Three places look very
different: a high-DER urban feeder, a low-income area with frequent outages, and a rural feeder with pumps and a clinic. One platform,
three kits. Everything you will see is **simulated**; nothing here commands equipment.

**0:30–1:15 — Urban (open the Urban kit).** Pick *Normal day*, then *EV surge*. Without coordination the transformer peaks at
105.2 % on the EV-surge day; with the optimiser 69.9 % (paired 95 % CI on the
reduction -38.3 to -32.4 pp). Open **Why did NEOS do this?** and read one entry.
Open the phase gateway: with 40 % of homes on one phase the worst phase reaches 171 % uncoordinated and
131 % with coordination. *Say:* phase-aware shedding gave no measurable advantage over phase-agnostic in this test.

**1:15–2:00 — Low-income resilience (Low-income kit).** Three arms on identical outage traces. A hub removes about
96 hours a year without critical supply. Adding flexibility trades -4.5 critical hours for +5.0 hours without *full*
essential supply: a trade-off, not a free win. *Say:* our model does **not** reproduce the earlier hub figures; the table shows both and the current numbers replace the old.

**2:00–2:30 — Rural (Rural kit).** An evening outage 17:00–23:00. Tier-blind shedding leaves the clinic without power for
6.0 hours; NEOS 0.0, with the same 20 kWh battery, because tier 1 is secured first. The community tier is only partly served
(5.0 hours without tier 1–2): the battery cannot carry everything. *Say:* the split between clinic hours and community lighting is a service-charter decision, not an optimisation result.

**2:30–2:50 — DISCOM interface (Low-income kit, DISCOM console).** The envelope reads *estimated available flexibility, not guaranteed
delivery*. The link is down 15:00–19:00; 1 command arrived expired and was rejected, never run late. Delivery is measured against a stated baseline, and the hash chain verifies
(verified). *Say:* it is a local hash chain, not a blockchain, and no payment or settlement is represented.

**2:50–3:00 — Close.** One platform, three kits, one safety layer, one evidence contract. What we did **not** do: run OpenDSS (the reference solver stands in), validate on a real
feeder, or build reinforcement learning. The evidence page lists every assumption.
