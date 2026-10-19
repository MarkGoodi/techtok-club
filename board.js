const problemForm = document.querySelector("#problem-form");
const problemList = document.querySelector("#problem-list");
const problemStatus = document.querySelector("#problem-status");
const problemCount = document.querySelector("#problem-count");

function createTextElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    return element;
}

async function loadProblems() {
    try {
        const response = await fetch("/api/problems");
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "The community board is unavailable.");

        problemList.replaceChildren();
        problemCount.textContent = `${result.problems.length} ${result.problems.length === 1 ? "challenge" : "challenges"}`;
        if (!result.problems.length) {
            problemList.append(createTextElement("p", "board-empty", "No challenges are public yet. Be the first to share one."));
            return;
        }
        result.problems.forEach((problem) => problemList.append(createProblemCard(problem)));
    } catch (error) {
        problemCount.textContent = "";
        problemList.replaceChildren(createTextElement(
            "p",
            "board-error",
            error instanceof Error ? error.message : "Could not load the community board. Please try again later."
        ));
    }
}

function createProblemCard(problem) {
    const card = document.createElement("article");
    card.className = "problem-card";
    card.append(createTextElement("h4", "problem-title", problem.title));
    card.append(createTextElement("p", "problem-description", problem.description));
    card.append(createTextElement("p", "problem-byline", `Shared by ${problem.name} · ${problem.comments.length} ${problem.comments.length === 1 ? "comment" : "comments"}`));

    const comments = document.createElement("ul");
    comments.className = "comment-list";
    if (!problem.comments.length) {
        comments.append(createTextElement("li", "comment-empty", "No comments yet. Share a thoughtful suggestion."));
    } else {
        problem.comments.forEach((comment) => {
            const item = document.createElement("li");
            item.append(createTextElement("strong", "comment-name", comment.name));
            item.append(createTextElement("p", "comment-body", comment.body));
            comments.append(item);
        });
    }
    card.append(comments);

    const form = document.createElement("form");
    form.className = "comment-form";
    form.hidden = !window.ttcAuthSession;
    form.innerHTML = `
        <label class="visually-hidden">Your comment
            <textarea name="body" rows="2" minlength="2" maxlength="1500" required placeholder="Suggest a solution or offer to help…"></textarea>
        </label>
        <label class="board-honeypot" aria-hidden="true">Leave this field empty<input name="website" tabindex="-1" autocomplete="off"></label>
        <button type="submit">Comment <span aria-hidden="true">↗</span></button>
        <p class="board-status" role="status" aria-live="polite"></p>`;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const body = form.elements.body.value.trim();
        form.elements.body.setCustomValidity(body ? "" : "Please enter a comment.");
        if (!form.reportValidity()) return;
        const button = form.querySelector("button");
        const status = form.querySelector(".board-status");
        button.disabled = true;
        try {
            const response = await window.ttcAuth.authorizedFetch(`/api/problems/${problem.id}/comments`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    body,
                    website: form.elements.website.value
                })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "Could not submit your comment.");
            status.textContent = result.message;
            form.reset();
        } catch (error) {
            status.textContent = error instanceof Error ? error.message : "Could not submit your comment.";
        } finally {
            button.disabled = false;
        }
    });
    card.append(form);
    return card;
}

problemForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = problemForm.elements.title.value.trim();
    const description = problemForm.elements.description.value.trim();
    problemForm.elements.title.setCustomValidity(title.length >= 5 ? "" : "Please enter a title with at least 5 characters.");
    problemForm.elements.description.setCustomValidity(description.length >= 20 ? "" : "Please describe the challenge in at least 20 characters.");
    if (!problemForm.reportValidity()) return;

    const button = problemForm.querySelector('button[type="submit"]');
    button.disabled = true;
    problemStatus.textContent = "";
    try {
        const response = await window.ttcAuth.authorizedFetch("/api/problems", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title,
                description,
                website: problemForm.elements.website.value
            })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not submit your challenge.");
        problemStatus.textContent = result.message;
        problemForm.reset();
    } catch (error) {
        problemStatus.textContent = error instanceof Error ? error.message : "Could not submit your challenge.";
    } finally {
        button.disabled = false;
    }
});

window.addEventListener("techtok-auth-change", (event) => {
    document.querySelectorAll(".comment-form").forEach((form) => {
        form.hidden = !event.detail.session;
    });
});

loadProblems();
