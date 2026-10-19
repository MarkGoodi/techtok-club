const signInButton = document.querySelector("#google-sign-in");
const signOutButton = document.querySelector("#google-sign-out");
const memberStatus = document.querySelector("#member-status");
const memberProfile = document.querySelector("#member-profile");
const profileForm = document.querySelector("#profile-form");
const displayNameInput = document.querySelector("#member-display-name");
const submissionsSection = document.querySelector("#member-submissions");
const submissionsList = document.querySelector("#my-submissions");
const problemForm = document.querySelector("#problem-form");
let supabaseClient = null;

function setMemberStatus(message, isError = false) {
    memberStatus.textContent = message;
    memberStatus.classList.toggle("is-error", isError);
}

async function getSession() {
    if (!supabaseClient) return null;
    const { data, error } = await supabaseClient.auth.getSession();
    if (error) throw error;
    return data.session;
}

async function authorizedFetch(url, options = {}) {
    const session = await getSession();
    if (!session?.access_token) throw new Error("Sign in with Google to continue.");
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${session.access_token}`);
    return fetch(url, { ...options, headers });
}

function makeSubmissionCard(kind, item) {
    const card = document.createElement("article");
    card.className = "member-submission";
    const heading = document.createElement("h5");
    heading.textContent = kind === "problem" ? item.title : `Comment on: ${item.problem_title}`;
    card.append(heading);
    const body = document.createElement("p");
    body.textContent = kind === "problem" ? item.description : item.body;
    card.append(body);
    const state = document.createElement("span");
    state.className = "submission-state";
    state.textContent = item.status === "approved" ? "Public" : "Waiting for review";
    card.append(state);
    const actions = document.createElement("div");
    actions.className = "submission-actions";

    const edit = document.createElement("button");
    edit.className = "member-action";
    edit.type = "button";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => {
        card.querySelector(".submission-edit")?.remove();
        const form = document.createElement("form");
        form.className = "submission-edit";
        if (kind === "problem") {
            const title = document.createElement("input");
            title.name = "title";
            title.value = item.title;
            title.minLength = 5;
            title.maxLength = 120;
            title.required = true;
            title.setAttribute("aria-label", "Problem title");
            const description = document.createElement("textarea");
            description.name = "description";
            description.value = item.description;
            description.minLength = 20;
            description.maxLength = 2000;
            description.required = true;
            description.setAttribute("aria-label", "Problem description");
            form.append(title, description);
        } else {
            const bodyInput = document.createElement("textarea");
            bodyInput.name = "body";
            bodyInput.value = item.body;
            bodyInput.minLength = 2;
            bodyInput.maxLength = 1500;
            bodyInput.required = true;
            bodyInput.setAttribute("aria-label", "Comment");
            form.append(bodyInput);
        }
        const save = document.createElement("button");
        save.className = "member-action";
        save.textContent = "Save edit";
        form.append(save);
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            save.disabled = true;
            try {
                const data = Object.fromEntries(new FormData(form));
                const response = await authorizedFetch("/api/account/items", {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ kind, id: item.id, ...data })
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || "Could not update your submission.");
                setMemberStatus(result.message);
                await loadAccount();
            } catch (error) {
                setMemberStatus(error instanceof Error ? error.message : "Could not update your submission.", true);
                save.disabled = false;
            }
        });
        card.append(form);
        form.querySelector("input, textarea").focus();
    });
    actions.append(edit);

    const remove = document.createElement("button");
    remove.className = "member-action member-delete";
    remove.type = "button";
    remove.textContent = "Delete";
    remove.addEventListener("click", async () => {
        if (!window.confirm("Delete this submission? This cannot be undone.")) return;
        remove.disabled = true;
        try {
            const response = await authorizedFetch("/api/account/items", {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ kind, id: item.id })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "Could not delete your submission.");
            setMemberStatus(result.message);
            await loadAccount();
        } catch (error) {
            setMemberStatus(error instanceof Error ? error.message : "Could not delete your submission.", true);
            remove.disabled = false;
        }
    });
    actions.append(remove);
    card.append(actions);
    return card;
}

async function loadAccount() {
    const response = await authorizedFetch("/api/account/items");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load your submissions.");
    submissionsList.replaceChildren();
    const entries = [
        ...result.problems.map((item) => ({ kind: "problem", item })),
        ...result.comments.map((item) => ({ kind: "comment", item }))
    ];
    if (!entries.length) {
        const empty = document.createElement("p");
        empty.textContent = "You haven’t shared anything yet.";
        submissionsList.append(empty);
    } else {
        entries.forEach(({ kind, item }) => submissionsList.append(makeSubmissionCard(kind, item)));
    }
    submissionsSection.hidden = false;
}

async function updateMemberState(session) {
    const signedIn = Boolean(session);
    window.ttcAuthSession = session;
    signInButton.hidden = signedIn;
    memberProfile.hidden = !signedIn;
    submissionsSection.hidden = !signedIn;
    problemForm.hidden = !signedIn;
    document.querySelectorAll(".comment-form").forEach((form) => {
        form.hidden = !signedIn;
    });
    window.dispatchEvent(new CustomEvent("techtok-auth-change", { detail: { session } }));
    if (!signedIn) {
        submissionsList.replaceChildren();
        setMemberStatus("Sign in with Google to share a challenge, join discussions, and manage your submissions.");
        return;
    }
    setMemberStatus(`Signed in as ${session.user.email || "a TechTok member"}.`);
    try {
        const response = await authorizedFetch("/api/account/profile");
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not load your profile.");
        displayNameInput.value = result.display_name || "";
        await loadAccount();
    } catch (error) {
        setMemberStatus(error instanceof Error ? error.message : "Could not load your account.", true);
    }
}

signInButton.addEventListener("click", async () => {
    if (!supabaseClient) {
        setMemberStatus("Google sign-in isn’t configured yet. The site owner must add the Supabase project URL and anon key, then enable Google in Supabase Auth.", true);
        return;
    }
    signInButton.disabled = true;
    setMemberStatus("Opening Google sign-in…");
    try {
        const { error } = await supabaseClient.auth.signInWithOAuth({
            provider: "google",
            options: { redirectTo: window.location.origin }
        });
        if (error) throw error;
    } catch (error) {
        setMemberStatus(error instanceof Error ? error.message : "Could not start Google sign-in.", true);
        signInButton.disabled = false;
    }
});

signOutButton.addEventListener("click", async () => {
    try {
        const { error } = await supabaseClient.auth.signOut();
        if (error) throw error;
        await updateMemberState(null);
    } catch (error) {
        setMemberStatus(error instanceof Error ? error.message : "Could not sign out.", true);
        return;
    }
});

profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const display_name = displayNameInput.value.trim();
    if (display_name.length < 2) {
        displayNameInput.setCustomValidity("Use at least 2 characters for your public display name.");
        displayNameInput.reportValidity();
        return;
    }
    displayNameInput.setCustomValidity("");
    const saveButton = profileForm.querySelector("button");
    saveButton.disabled = true;
    try {
        const response = await authorizedFetch("/api/account/profile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ display_name })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not save your display name.");
        setMemberStatus("Your public display name has been saved.");
    } catch (error) {
        setMemberStatus(error instanceof Error ? error.message : "Could not save your display name.", true);
    } finally {
        saveButton.disabled = false;
    }
});

if (window.supabase?.createClient && window.TECHTOK_SUPABASE_CONFIG?.url && window.TECHTOK_SUPABASE_CONFIG?.anonKey) {
    supabaseClient = window.supabase.createClient(
        window.TECHTOK_SUPABASE_CONFIG.url,
        window.TECHTOK_SUPABASE_CONFIG.anonKey
    );
    supabaseClient.auth.onAuthStateChange((_event, session) => {
        window.setTimeout(() => updateMemberState(session), 0);
    });
    getSession().then(updateMemberState).catch((error) => {
        setMemberStatus(error.message || "Could not check your sign-in.", true);
    });
} else {
    signInButton.disabled = false;
    problemForm.hidden = true;
    setMemberStatus("Google sign-in isn’t configured yet. The site owner must add the Supabase project URL and anon key, then enable Google in Supabase Auth.", true);
}

window.ttcAuth = { getSession, authorizedFetch };
