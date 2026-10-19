const loginForm = document.querySelector("#moderation-login");
const passwordInput = document.querySelector("#moderation-password");
const loginStatus = document.querySelector("#moderation-status");
const queue = document.querySelector("#moderation-queue");
const moderationItems = document.querySelector("#moderation-items");

function moderationCard(kind, item) {
    const card = document.createElement("article");
    card.className = "moderation-card";
    card.append(Object.assign(document.createElement("span"), {
        className: "moderation-kind",
        textContent: kind === "problem" ? "PROBLEM" : `COMMENT · ${item.problem_title}`
    }));
    card.append(Object.assign(document.createElement("h3"), {
        textContent: kind === "problem" ? item.title : item.name
    }));
    if (kind === "problem") {
        card.append(Object.assign(document.createElement("p"), { textContent: `Shared by ${item.name}` }));
        card.append(Object.assign(document.createElement("p"), { textContent: item.description }));
    } else {
        card.append(Object.assign(document.createElement("p"), { textContent: `Comment by ${item.name}` }));
        card.append(Object.assign(document.createElement("p"), { textContent: item.body }));
    }
    const actions = document.createElement("div");
    actions.className = "moderation-actions";
    actions.append(actionButton("Approve", "approve", kind, item.id));
    actions.append(actionButton("Delete", "delete", kind, item.id));
    card.append(actions);
    return card;
}

function actionButton(label, action, kind, id) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = action === "approve" ? "button button-primary" : "button moderation-delete";
    button.textContent = label;
    button.addEventListener("click", async () => {
        if (action === "delete" && !window.confirm("Delete this submission permanently?")) return;
        button.disabled = true;
        try {
            const response = await fetch("/api/moderation/action", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Moderation-Action": "true"
                },
                body: JSON.stringify({ action, kind, id })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "Could not update this submission.");
            await loadQueue();
        } catch (error) {
            loginStatus.textContent = error instanceof Error ? error.message : "Could not update this submission.";
            button.disabled = false;
        }
    });
    return button;
}

async function loadQueue() {
    try {
        const response = await fetch("/api/moderation/queue");
        const result = await response.json();
        if (response.status === 401) {
            queue.hidden = true;
            loginForm.hidden = false;
            return;
        }
        if (!response.ok) throw new Error(result.error || "Could not load the moderation queue.");
        moderationItems.replaceChildren();
        const pendingItems = [
            ...result.problems.map((item) => moderationCard("problem", item)),
            ...result.comments.map((item) => moderationCard("comment", item))
        ];
        if (pendingItems.length) moderationItems.append(...pendingItems);
        else moderationItems.append(Object.assign(document.createElement("p"), {
            className: "moderation-empty",
            textContent: "All caught up. No submissions are waiting for review."
        }));
        loginStatus.textContent = "";
        loginForm.hidden = true;
        queue.hidden = false;
    } catch (error) {
        loginStatus.textContent = error instanceof Error ? error.message : "Could not load the moderation queue.";
    }
}

async function checkSession() {
    try {
        const response = await fetch("/api/moderation/session");
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not check sign-in status.");
        if (result.authenticated) await loadQueue();
    } catch (error) {
        loginStatus.textContent = error instanceof Error ? error.message : "Could not check sign-in status.";
    }
}

loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    loginStatus.textContent = "";
    try {
        const response = await fetch("/api/moderation/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: passwordInput.value })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not sign in.");
        passwordInput.value = "";
        await loadQueue();
    } catch (error) {
        loginStatus.textContent = error instanceof Error ? error.message : "Could not sign in.";
    }
});

document.querySelector("#moderation-logout").addEventListener("click", async () => {
    try {
        await fetch("/api/moderation/logout", { method: "POST" });
    } finally {
        queue.hidden = true;
        loginForm.hidden = false;
        loginStatus.textContent = "You are signed out.";
    }
});

checkSession();
