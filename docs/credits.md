# Sources and credit

These tools combine local evidence ledgers, process supervision and policy checks. The following public work supplies context for ideas used in the workflow. There is no claim that this repository implements or reproduces those research systems.

- Cynthia Dwork and coauthors, [Generalization in Adaptive Data Analysis and Holdout Reuse](https://arxiv.org/abs/1506.02629), 2015: adaptive reuse of evaluation data can undermine validity. The harness records a holdout disposition; the paper's reusable-holdout algorithm is not implemented.
- Avrim Blum and Moritz Hardt, [The Ladder: A Reliable Leaderboard for Machine Learning Competitions](https://arxiv.org/abs/1502.04585), 2015: repeated public feedback can overfit a competition leaderboard. Selection-service isolation and query policy remain external to this code; Ladder is not implemented.
- Google DeepMind and coauthors, [AlphaEvolve: A coding agent for scientific and algorithmic discovery](https://arxiv.org/abs/2506.13131), 2025: related work on program proposal and automated evaluation. This harness provides source intake, experiment receipts and bounded workers rather than an evolutionary search implementation.
- Edwin B. Wilson, [Probable Inference, the Law of Succession, and Statistical Inference](https://doi.org/10.1080/01621459.1927.10502953), 1927: the role ranker uses the lower endpoint of the Wilson score interval with z = 1.96.

Source links were checked on 2026-11-03. Python, pytest, Git, zsh and the optional provider CLIs retain their own licenses and attribution. No third-party package source is vendored here.
