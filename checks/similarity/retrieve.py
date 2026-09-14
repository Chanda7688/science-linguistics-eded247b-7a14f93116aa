#!/usr/bin/env python3
"""Lexical retrieval for the duplicate-task check (stdlib only — no pip installs).

Reads the submitted instruction.md and the baked TB2/TB3 corpus, ranks corpus
tasks by TF-IDF cosine similarity to the submission, and assembles a self-contained
prompt for the LLM duplicate-judge (header + submission + the top-K candidates).

The retrieval just NARROWS 137 tasks down to the few worth a careful look; the
LLM judge makes the actual duplicate/unique call. Lexical scores are crude
(paraphrases score lower), which is exactly why a judge reads the finalists.

Usage:
    python3 retrieve.py \
        --corpus  .dynamo/similarity/tb_corpus.jsonl \
        --submission submission/task/instruction.md \
        --header  .dynamo/similarity/judge-prompt.md \
        --top-k 6 \
        --matches-out matches.md \
      > judge-prompt.full.md            # the assembled prompt (pipe to claude -p)
"""
import argparse
import json
import math
import re
import sys
from collections import Counter

STOP = set("""a an the of to in on for and or is are be this that your you it its with
as at by from into will should must can each per via no not any all use using used
task solution file files output outputs value values must may which their them then
""".split())

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text):
    return [t for t in TOKEN_RE.findall(text.lower())
            if len(t) > 2 and t not in STOP]


def tfidf(tokens, idf):
    tf = Counter(tokens)
    n = len(tokens) or 1
    return {t: (c / n) * idf.get(t, 0.0) for t, c in tf.items()}


def cosine(a, b):
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--submission", required=True)
    ap.add_argument("--header", required=True)
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--matches-out", help="write a human-readable match summary here")
    args = ap.parse_args()

    with open(args.submission, encoding="utf-8", errors="replace") as f:
        sub_text = f.read().strip()
    sub_tokens = tokenize(sub_text)
    if not sub_tokens:
        sys.exit("submission instruction.md is empty or has no usable text")

    corpus = []
    with open(args.corpus, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                corpus.append(json.loads(line))

    # IDF over corpus + submission
    docs_tokens = [set(tokenize(c["instruction"])) for c in corpus]
    docs_tokens.append(set(sub_tokens))
    N = len(docs_tokens)
    df = Counter()
    for toks in docs_tokens:
        df.update(toks)
    idf = {t: math.log(N / (1 + dfc)) + 1.0 for t, dfc in df.items()}

    sub_vec = tfidf(sub_tokens, idf)
    scored = []
    for c in corpus:
        vec = tfidf(tokenize(c["instruction"]), idf)
        scored.append((cosine(sub_vec, vec), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:args.top_k]

    if args.matches_out:
        with open(args.matches_out, "w", encoding="utf-8") as m:
            m.write("| Rank | Existing task | Benchmark | Lexical similarity |\n")
            m.write("|------|---------------|-----------|--------------------|\n")
            for i, (score, c) in enumerate(top, 1):
                m.write(f"| {i} | `{c['task']}` | {c['source']} | {score:.3f} |\n")

    with open(args.header, encoding="utf-8") as f:
        header = f.read()

    out = [header.rstrip(), ""]
    out.append("## SUBMITTED TASK (the one under review)")
    out.append("")
    out.append("<submitted_instruction>")
    out.append(sub_text)
    out.append("</submitted_instruction>")
    out.append("")
    out.append(f"## EXISTING BENCHMARK TASKS — the {len(top)} most lexically similar")
    out.append("")
    for i, (score, c) in enumerate(top, 1):
        out.append(f"### Candidate {i}: {c['source']} / {c['task']} (lexical score {score:.3f})")
        out.append("")
        out.append("<existing_instruction>")
        out.append(c["instruction"])
        out.append("</existing_instruction>")
        out.append("")
    sys.stdout.write("\n".join(out))


if __name__ == "__main__":
    main()
