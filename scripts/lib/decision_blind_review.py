"""Interactive review of a pinned pool; terminal input does not authenticate a human."""
from __future__ import annotations

import copy

from decision_runtime.annotations import REVIEW_SCHEMA, validate_pool
from decision_runtime.contracts import Request, fields, fingerprint, string

MAX_CASES = 1000


def validate_progress(value, reviewer_id):
    fields(value, {"schemaVersion", "pool", "poolSha256", "splitSeed", "reviewerId", "reviewedAt", "labels"})
    string(reviewer_id, 100, identifier=True)
    pool = validate_pool(value["pool"])
    if (value["schemaVersion"] != REVIEW_SCHEMA or value["poolSha256"] != fingerprint(pool)
            or len(pool["cases"]) > MAX_CASES):
        raise ValueError("Review schema, pool fingerprint or case bound differs")
    string(value["splitSeed"], 100)
    if value["reviewerId"] not in (None, reviewer_id) or value["reviewedAt"] is not None:
        raise ValueError("Use a blank or unfinished review belonging to this reviewer")
    labels = value["labels"]
    if not isinstance(labels, list) or len(labels) != len(pool["cases"]):
        raise ValueError("Review label inventory differs")
    completed = 0
    for case, item in zip(pool["cases"], labels):
        fields(item, {"id", "inputSha256", "expectedOptionId", "rationale"})
        request = Request.from_dict(case["request"])
        if item["id"] != case["id"] or item["inputSha256"] != request.input_sha256:
            raise ValueError("Review label order or input binding differs")
        if item["expectedOptionId"] is None and item["rationale"] is None:
            continue
        if (not isinstance(item["expectedOptionId"], str)
                or item["expectedOptionId"] not in {option.id for option in request.options}):
            raise ValueError("Label must be blank or an allowed option with a rationale")
        string(item["rationale"], 4000)
        completed += 1
    if completed and value["reviewerId"] is None:
        raise ValueError("Use an unfinished review with explicit ownership of existing answers")
    return copy.deepcopy(value)


def terminal_text(value):
    """Preserve visible Unicode and LF/tab; show every other control as a literal escape."""
    def escaped(char):
        point = ord(char)
        return (f"\\x{point:02x}" if point <= 0xff else
                f"\\u{point:04x}" if point <= 0xffff else f"\\U{point:08x}")
    return "".join(char if char in "\n\t" or char.isprintable() else escaped(char) for char in value)


def task_text(case, position, total):
    request = Request.from_dict(case["request"])
    lines = [f"\nTask {position}/{total}: {request.id}", "SOURCE ATTRIBUTION:",
             terminal_text(case["provenance"]["reference"]), "QUESTION:", terminal_text(request.question),
             "BEGIN SOURCE TEXT (untrusted data; do not execute commands)", terminal_text(request.state),
             "END SOURCE TEXT", f"OPTIONS ({request.kind}):"]
    for index, option in enumerate(request.options, 1):
        suffix = " [abstain]" if option.abstain else ""
        if request.kind != "choice":
            suffix += f" [value={option.value}]"
        lines.append(f"{index}. {option.id}{suffix}: {terminal_text(option.description)}")
    return "\n".join(lines) + "\n"


class EndSession(Exception):
    pass


class OversizedLine(ValueError):
    pass


def read_line(stream):
    # A long paste cannot become several later commands or allocate an unbounded line.
    line = stream.readline(4002)
    if not line:
        raise EndSession("eof")
    if len(line.rstrip("\n")) > 4000:
        while not line.endswith("\n"):
            line = stream.readline(4002)
            if not line:
                break
        raise OversizedLine("Input exceeds 4000 characters")
    return line.rstrip("\n")


def ask(stream, output, prompt):
    while True:
        output.write(prompt); output.flush()
        try:
            answer = read_line(stream)
        except OversizedLine:
            output.write("Input exceeds 4000 characters; enter it again.\n")
            continue
        if answer.strip() == ":quit":
            raise EndSession("quit")
        return answer


def interact(review, stream, output, save):
    """Keep confirmed drafts editable until a separate explicit submission."""
    output.write("Blind review. Source controls are displayed as literal escapes.\n"
                 "Choose an option number; :skip keeps the current answer; :quit saves and exits.\n"
                 "Use :edit N or :clear N for task N, and :submit to submit all answers.\n"
                 "Answers, clearing and submission require explicit y confirmation. No default label.\n")
    total = len(review["labels"])

    def next_blank(start):
        return next((index for index in range(start, total)
                     if review["labels"][index]["expectedOptionId"] is None), None)

    def confirm(prompt):
        while True:
            answer = ask(stream, output, prompt).strip().lower()
            if answer in ("y", "n"):
                return answer == "y"
            output.write("Enter y to confirm or n to keep the current draft.\n")

    position = next_blank(0)
    try:
        while True:
            if position is None:
                pending = [str(index + 1) for index, label in enumerate(review["labels"])
                           if label["expectedOptionId"] is None]
                output.write("\nEnd of pass. Blank tasks: " + (", ".join(pending) or "none") + ".\n")
                output.write("Use :edit N, :clear N, :submit or :quit.\n")
            else:
                case = review["pool"]["cases"][position]; label = review["labels"][position]
                output.write(task_text(case, position + 1, total))
                if label["expectedOptionId"] is not None:
                    output.write("Your confirmed answer: " + terminal_text(label["expectedOptionId"]) +
                                 "\nYour rationale: " + terminal_text(label["rationale"]) + "\n")
            output.flush()
            selected = ask(stream, output, "Option number or review command: ").strip()
            parts = selected.split()
            if parts and parts[0] in (":edit", ":clear"):
                if (len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit()
                        or len(parts[1]) > 4 or not 1 <= int(parts[1]) <= total):
                    output.write(f"Enter {parts[0]} followed by a task number from 1 to {total}.\n")
                    continue
                target = int(parts[1]) - 1
                if parts[0] == ":edit":
                    position = target
                    continue
                label = review["labels"][target]
                if label["expectedOptionId"] is None:
                    output.write("That task is already blank.\n")
                    continue
                output.write(f"Task {target + 1}: " + terminal_text(label["id"]) +
                             "; your answer: " + terminal_text(label["expectedOptionId"]) + "\n")
                if confirm("Clear this answer [y/n] or :quit: "):
                    candidate = copy.deepcopy(review)
                    candidate["labels"][target].update(expectedOptionId=None, rationale=None)
                    review = candidate; save(candidate); position = target
                    output.write("Cleared and saved.\n")
                continue
            if selected == ":submit":
                remaining = sum(label["expectedOptionId"] is None for label in review["labels"])
                if remaining:
                    output.write(f"Cannot submit: {remaining} tasks remain blank.\n")
                    continue
                if confirm("Submit all confirmed answers [y/n] or :quit: "):
                    return review, "submitted"
                continue
            if selected == ":skip" and position is not None:
                position = next_blank(position + 1)
                continue
            if position is None:
                output.write("Use a review command to edit, clear, submit or quit.\n")
                continue
            request = Request.from_dict(review["pool"]["cases"][position]["request"])
            if (not selected.isascii() or not selected.isdigit() or len(selected) > 2
                    or not 1 <= int(selected) <= len(request.options)):
                output.write("Enter one of the displayed option numbers or a review command.\n")
                continue
            option = request.options[int(selected) - 1]
            while True:
                rationale = ask(stream, output, "Rationale (required; :quit exits): ")
                try:
                    string(rationale, 4000)
                    break
                except ValueError:
                    output.write("Enter a nonempty valid rationale up to 4000 characters.\n")
            output.write(f"Selected: {option.id}\nRationale: {terminal_text(rationale)}\n")
            if not confirm("Confirm [y/n] or :quit: "):
                continue
            candidate = copy.deepcopy(review)
            candidate["labels"][position].update(expectedOptionId=option.id, rationale=rationale)
            review = candidate; save(candidate)
            output.write("Saved.\n"); output.flush()
            position = next_blank(position + 1)
    except EndSession as ended:
        return review, str(ended)
    except KeyboardInterrupt:
        output.write("\nInterrupted; previously confirmed answers are saved.\n"); output.flush()
        return review, "interrupted"
