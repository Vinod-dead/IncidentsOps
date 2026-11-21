
# Papers behind the design

The following papers and engineering resources informed the
design. The implementation connections below describe how
their ideas relate to this project, not claims of reproducing
the original research.

| Paper | Used for |
|---|---|
| [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629) (Yao et al., 2022) | Thought/action/observation loop in analyst agents |
| [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) (Shinn et al., 2023) | Inspiration for feedback-driven plan revision |
| [Self-Refine: Iterative Refinement with Self-Feedback](https://arxiv.org/abs/2303.17651) (Madaan et al., 2023) | Inspiration for bounded iterative refinement |
| [AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation](https://arxiv.org/abs/2308.08155) (Wu et al., 2023) | Multi-agent conversation and delegation patterns |
| [MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework](https://arxiv.org/abs/2308.00352) (Hong et al., 2023) | Role-specialized agents and structured hand-offs |
| [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657) (Cemri et al., 2025) | Motivation for guards, budgets, and failure handling |
| [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685) (Zheng et al., 2023) | Judge methodology and known biases; supports keeping deterministic checks primary |
| [Building effective agents](https://www.anthropic.com/research/building-effective-agents) (Anthropic, 2024) | Orchestrator-workers pattern and preference for simple, composable workflows |

