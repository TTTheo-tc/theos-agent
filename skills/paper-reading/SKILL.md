---
name: paper-reading
description: Perform single-paper deep analysis with a fixed workflow: problem → core idea → method breakdown → core code reconstruction → experimental conclusions → value judgment. Use when the user asks for paper reading, paper analysis, method breakdown, paper-based code reconstruction, or source-code comparison after deriving the method from the paper.
---

# Paper Reading

## Use this skill when

Use this skill for **single-paper deep analysis** when the user asks for any of the following:

- paper reading / 论文阅读
- analyze a paper
- break down a paper's method
- reconstruct the core idea in code
- compare paper-derived code against the official implementation

If the user wants a series overview, a literature survey, or a follow-up-work landscape, use this workflow as a reference but do not force the structure mechanically.

---

## Core workflow

Follow this fixed reading path:

1. **Core idea extraction**
2. **Method breakdown**
3. **Core code reconstruction**
4. **Experimental conclusions**
5. **Value judgment**
6. **One-paragraph / one-line takeaway**

---

## Standard output structure

### Paper info
- **Paper**: full paper title
- **Authors**: author list
- **Link**: arXiv / conference / journal URL
- **Code**: official repo / project page (if any)
- **Venue**: conference / year
- **Reading basis**: PDF / HTML / appendix / code / supplementary used for the analysis

### 1. Core idea extraction
Answer these questions:
- What problem does the paper solve?
- What is the author's core idea?
- What is the most fundamental difference from prior work?

### 2. Method breakdown
Include at least two levels:
- **Overall pipeline**: input → intermediate processing → output
- **Key modules**: for each important module, explain:
  - what it does
  - how it works
  - why it is designed that way

### 3. Core code reconstruction
Always split this section into three parts:
- **3.1 Core code derived from the paper**
- **3.2 Official implementation comparison**
- **3.3 Difference and reason analysis**

### 4. Experimental conclusions
Answer these questions:
- What are the main experimental conclusions?
- What do ablations / comparisons actually show?
- Do the experiments really support the paper's claims?

### 5. Value judgment
Answer these questions:
- What are the real contributions?
- What are the limitations?
- What is the overall evaluation?

### 6. Final takeaway
Use 1-3 sentences to answer:
- Is the paper worth reading?
- What is the most important thing to remember?
- Who is the right audience for it?

---

## Hard rule: core code reconstruction

This is a strict rule and must be followed exactly.

### Required order

1. **Derive the core code from the paper first**
   - Use only the paper text, figures, equations, and appendix
   - Write an independent code skeleton / pseudocode / minimal implementation idea

2. **Do not read the official implementation before finishing the paper-derived reconstruction**
   - do not inspect the repo first
   - do not mix source-informed understanding into the paper-only reconstruction
   - do not skip the independent derivation stage

3. **Only after the reconstruction is complete, read the official code**
   - locate the key modules
   - compare the real implementation
   - identify omitted engineering details

4. **Finally analyze the differences and their causes**
   - what matches
   - what differs
   - why those differences exist

### Forbidden

- reading the source code first and back-solving the paper logic
- presenting source-informed understanding as if it came only from the paper
- omitting the difference analysis after source comparison

The only acceptable sequence is:

**paper → independent code derivation → source comparison → difference analysis**

---

## Writing principles

### 1. Put conclusions before details
Let the reader know what the paper is about and whether it matters before diving into technical detail.

### 2. Explain in plain language before equations
When breaking down methods, explain module purpose first, then implementation details, formulas, and design rationale.

### 3. Do not just restate; make judgments
In the experimental and value sections, clearly separate:
- what the paper claims
- what the evidence actually supports
- what your own judgment is

### 4. Keep the skeleton fixed, not the length
Unify the reading path, not the verbosity. Simple papers can stay short; complex papers can expand.

### 5. If the user wants beginner-friendly analysis
Start each major section with one or two plain-language sentences before going deeper.

---

## Minimal template

Use this skeleton when needed:

```markdown
# {{paper title}} paper analysis

> **Paper**: {{full title}}
> **Authors**: {{authors}}
> **Link**: {{paper link}}
> **Code**: {{code link}}
> **Venue**: {{conference/year}}
> **Reading basis**: {{PDF / HTML / appendix / code}}

---

## 1. Core idea extraction
### 1.1 What problem does it solve?
### 1.2 What is the core idea?
### 1.3 What is the key difference from prior work?

## 2. Method breakdown
### 2.1 Overall pipeline
### 2.2 Key modules

## 3. Core code reconstruction
### 3.1 Core code derived from the paper
### 3.2 Official implementation comparison
### 3.3 Difference and reason analysis

## 4. Experimental conclusions
### 4.1 Main results
### 4.2 What do ablations / comparisons show?
### 4.3 Do the experiments support the claims?

## 5. Value judgment
### 5.1 Real contributions
### 5.2 Limitations
### 5.3 Overall evaluation

## 6. Final takeaway
```
