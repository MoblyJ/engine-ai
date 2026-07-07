---
name: code-architecture
description: Design the OOP structure (classes, responsibilities, relationships) of a new module/feature BEFORE writing any implementation — grounded in real-world precedent (web_search + StackOverflow), expressed as a Mermaid class diagram, optimized for modularity, reuse, fast delivery, and zero duplicate code. Use before implementing a non-trivial new module or feature, not for one-line fixes or bugs (that's `debugging`).
---

# Code Architecture

Don't start writing classes from a blank page — **research how well-established codebases solve this
shape of problem**, then design the class structure as a diagram, THEN implement. A diagram costs
minutes; a wrong class structure costs a rewrite.

## When to use
- Building a new module, subsystem, or non-trivial feature that involves more than one responsibility
  (i.e. it's naturally more than one function) — new integrations, new abstractions, new subsystems.
- NOT for a one-line fix or a single error (`debugging`), and not for stable "how should I architect
  a whole system" questions unrelated to a concrete build (`expert-answer`).

## Method
1. **Research precedent** — `web_search({ query })` for established design patterns/architecture
   examples for this shape of problem, and `so_search({ query, tagged })` for how real engineers
   structured similar problems on StackOverflow (accepted/high-score answers are precedent, not just
   opinion). Don't reinvent a wheel that already has a well-known shape (e.g. Strategy for pluggable
   backends, Facade for a unified interface over several subsystems).
2. **Design before code** — propose the class structure as a **Mermaid `classDiagram`**: classes,
   their key methods/attributes, and relationships (composition `*--`, inheritance `<|--`,
   dependency `..>`). Optimize for:
   - **Single responsibility** — each class does one coherent thing.
   - **Reuse over duplication** — shared behavior lives in one class/base/utility, never copy-pasted
     across two call sites. If you're about to write near-identical logic twice, that's a class/
     function boundary you're missing.
   - **Dependency injection over hard-coded dependencies** — so components are swappable/testable
     (e.g. inject a data source rather than importing it directly inside a class that uses it).
   - **Small, composable pieces** — prefer several small classes with clear boundaries over one large
     class doing everything; this is what makes the result reusable and fast to extend later.
3. **Get the diagram right before implementing.** If the task is being handed to another agent
   (e.g. `engine-app-builder`), hand off the diagram + the precedent that grounds it — don't make the
   builder re-derive the design.
4. **Implement following the diagram** — resist scope creep; don't add classes/abstractions the
   diagram didn't call for. Keep methods short and each class's public interface small.
5. **Remember** — `memory_save([...topic keywords], "<design decision + why>", { diagram: "...",
   sources: [...] })` so a related future feature reuses this design instead of re-deriving it.

## Red flags
- Writing implementation before a class diagram exists for a non-trivial new module.
- Two near-identical blocks of logic that should have been one shared method/class.
- A class diagram nobody grounded in precedent (invented from scratch when a well-known pattern fits).
- Classes with vague, overlapping responsibilities ("Manager", "Helper", "Utils" doing five things).

## Verification
- [ ] Researched precedent (`web_search` / `so_search`) before designing the class structure
- [ ] Produced a Mermaid `classDiagram` before implementation started
- [ ] No duplicated logic — shared behavior factored into one place
- [ ] Design decision saved to memory for reuse on related future work
