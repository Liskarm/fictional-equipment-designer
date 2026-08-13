# Fictional Equipment Designer

一个用于虚构世界观创作的 Codex Skill，面向小说、游戏、动画、概念设计、COS 道具概念和世界观设定集。它可以设计、修订、比较和审查虚构武器、护甲、工具、载具、遗物、魔法装置及装备家族。

## 独特特性

- **先审查“为什么存在”**：从操作者、任务、使用环境和既有装备出发，判断新装备是否解决了真实的设定问题；若定位重叠，就调整角色、资源循环或失败方式，而不是简单叠加性能。
- **建立能力闭环**：每项主要能力都要对应原理、资源消耗、可见后果、故障模式和反制手段，避免用术语密度掩盖因果缺口。
- **把装备放回使用者手中**：覆盖携行、准备、启动、控制、补充资源、故障处置、维护和运输，并考虑体型、护具、训练、疲劳及团队协作。
- **让功能、玩法与外观相互解释**：战斗角色、节奏、反馈和反制决定装备如何使用；轮廓、材质、标记和磨损同时表达功能、阵营、时代与生产方式。
- **审查完整生命周期与世界影响**：追踪制造、部署、维护、改型和淘汰，以及技术扩散后对战术、产业、法律、基础设施、文化和日常生活产生的多阶影响。
- **区分事实与创作**：以 `SOURCE`、`USER`、`INFERENCE`、`PROPOSAL` 和 `UNKNOWN` 标记信息来源，在既有世界观中显式处理设定冲突，并可维护持久化装备正史库。
- **以验收门槛收尾**：交付前检查定位、弱点、资源链、操作性、反制、后勤、视觉识别、变体取舍、世界影响、正史一致性和安全边界。

## 工作流程与各部分作用

`设计契约 → 任务路由 → 设计命题与存在必要性 → 机制/资源/操作循环 → 战斗与视觉表达 → 生命周期与变体 → 世界影响与正史 → 综合审查 → 交付`

| 部分 | 作用 |
|---|---|
| `SKILL.md` | 识别任务模式，建立设计契约，编排完整流程，并规定输出、证据标签、验收与安全边界。 |
| `mechanism-and-lifecycle.md` | 检查能力因果链、资源与约束、操作循环、故障、生产维护和变体取舍。 |
| `combat-and-balance.md` | 定义战术或玩法角色、行动节奏、信息反馈、反制阶梯和调优空间。 |
| `visual-language.md` | 将功能、操作者、阵营、时代和生命周期转化为轮廓、形态、材质、色彩与标记。 |
| `world-impact-and-canon.md` | 推演技术传播的直接、系统性与长期后果，管理证据、关系、冲突及正史状态。 |
| `audit-rubric.md` | 汇总交叉检查，识别阻断问题，并把尚未解决的假设和决策明确交给创作者。 |
| `equipment_registry.py` | 对持久化装备正史库执行新增、更新、索引、冲突记录与一致性验证。 |

Skill 会按请求选择最轻量的工作模式，可输出快速概念、完整装备档案、装备家族、审查修订、视觉简报、世界影响分析或正史条目。目标不是只让装备“看起来很酷”，而是让它具有清楚的存在理由，能够被使用、维护、反制，并对世界产生与其能力相称的影响。

本 Skill 仅用于虚构创作、游戏系统、视觉开发和明显非功能性的道具概念，不提供现实武器制造或改装指导。

## License

本项目采用 [GNU General Public License v3.0](LICENSE)。

---

## English

A Codex Skill for fictional worldbuilding across novels, games, animation, concept design, cosplay prop concepts, and world bibles. It designs, revises, compares, and audits fictional weapons, armor, tools, vehicles, artifacts, magical devices, and equipment families.

### Distinctive features

- **It asks why an item needs to exist first:** the operator, task, environment, and existing equipment determine whether the item solves a genuine setting problem. Overlap is fixed by changing its role, resource loop, or failure profile—not merely by raising its power.
- **It closes the capability loop:** every major capability needs a principle, resource cost, visible consequence, failure mode, and counterplay, preventing technical language from hiding gaps in causality.
- **It designs from the operator's experience:** carry, preparation, activation, control, replenishment, malfunction response, maintenance, and transport are considered alongside anatomy, armor, training, fatigue, and teamwork.
- **Function, play, and appearance explain one another:** combat role, tempo, feedback, and counters shape use, while silhouette, materials, markings, and wear communicate function, faction, era, and production culture.
- **It audits the full lifecycle and wider world:** production, deployment, upkeep, variants, and obsolescence lead into multi-order effects on doctrine, industry, law, infrastructure, culture, and everyday life.
- **It separates facts from invention:** `SOURCE`, `USER`, `INFERENCE`, `PROPOSAL`, and `UNKNOWN` labels preserve provenance, expose canon conflicts, and support a persistent equipment canon registry.
- **It ends with an acceptance gate:** role, weakness, resource chain, operability, counterplay, logistics, visual identity, variant tradeoffs, world impact, canon consistency, and safety are checked before delivery.

### Workflow and component roles

`Design contract → Task routing → Thesis and necessity → Mechanism/resources/operator loop → Combat and visual language → Lifecycle and variants → World impact and canon → Integrated audit → Delivery`

| Component | Role |
|---|---|
| `SKILL.md` | Selects the task mode, establishes the design contract, orchestrates the workflow, and defines output, evidence, acceptance, and safety rules. |
| `mechanism-and-lifecycle.md` | Audits capability causality, resources, constraints, operator loops, failures, production, maintenance, and variant tradeoffs. |
| `combat-and-balance.md` | Defines tactical or gameplay roles, action tempo, feedback, counterplay ladders, and tuning space. |
| `visual-language.md` | Translates function, operator, faction, era, and lifecycle into silhouette, form, materials, color, and markings. |
| `world-impact-and-canon.md` | Traces direct, systemic, and long-term consequences while managing evidence, relationships, conflicts, and canon status. |
| `audit-rubric.md` | Performs the final cross-check, identifies blockers, and exposes unresolved assumptions and creator decisions. |
| `equipment_registry.py` | Adds, updates, indexes, records conflicts, and validates a persistent equipment canon registry. |

The Skill selects the lightest mode appropriate to the request and can deliver a rapid concept, full dossier, equipment family, audit or revision, visual brief, world-impact analysis, or canon entry. Its goal is not merely to make equipment look cool, but to give it a clear reason to exist, make it usable, maintainable, and counterable, and ensure its effect on the setting matches the reach of its capabilities.

This Skill is intended only for fictional creation, game systems, visual development, and clearly nonfunctional prop concepts. It does not provide guidance for constructing or modifying real-world weapons.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
