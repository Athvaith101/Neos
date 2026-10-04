# Literature matrix (template)

Fill one row per paper **after reading it**. The "seed" rows below are well-known references included to anchor the
structure; their bibliographic details are written from memory and must be checked before citation. No row has been
read on your behalf.

| Category | Source | Data / setting | Method | Constraints handled | Reported metric | Limitation | Relevance to NEOS | Read? |
|---|---|---|---|---|---|---|---|---|
| Forecasting | Lim, Arık, Loeff, Pfister. *Temporal Fusion Transformers for interpretable multi-horizon time series forecasting.* Int. J. Forecasting, 2021 | | | | | | Candidate to beat `quantile_gbm+conformal` | no |
| Forecasting | Nie et al. *A Time Series is Worth 64 Words* (PatchTST). ICLR 2023 | | | | | | Candidate; needs real multi-year data | no |
| Forecast scoring | Gneiting & Raftery. *Strictly proper scoring rules, prediction, and estimation.* JASA, 2007 | | | | | | Why mean pinball ≠ CRPS | no |
| Calibration | Romano, Patterson, Candès. *Conformalized quantile regression.* NeurIPS 2019 | | | | | | Basis of `QuantileGBM` widening; exchangeability caveat | no |
| Safe RL | Achiam, Held, Tamar, Abbeel. *Constrained Policy Optimization.* ICML 2017 | | | | | | Candidate constrained-RL baseline | no |
| Safe RL | Alshiekh et al. *Safe Reinforcement Learning via Shielding.* AAAI 2018 | | | | | | Conceptual match to the safety projection | no |
| MARL | Lowe et al. *MADDPG.* NeurIPS 2017 | | | | | | Candidate agent algorithm | no |
| MARL | Rashid et al. *QMIX.* ICML 2018 | | | | | | Candidate for cooperative value factorisation | no |
| DR / RL review | Vázquez-Canteli & Nagy. *Reinforcement learning for demand response: a review.* Applied Energy, 2019 | | | | | | Survey entry point | no |
| Grid optimisation | Farivar & Low. *Branch flow model: relaxations and convexification.* IEEE TPWRS, 2013 | | | | | | Route to network-constrained MPC instead of a linear voltage proxy | no |

## Still to search (from the project plan)
A. probabilistic load forecasting; B. probabilistic PV / NWP / Indian solar; C. EV departure and flexibility modelling;
D. distribution-grid digital twins and DER management; E. MARL for microgrids, safe/hierarchical MARL.
Record for every paper: dataset, assumptions, constraints, reported metric, limitation, and what it implies for NEOS.
