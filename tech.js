const menuToggle = document.querySelector(".menu-toggle");
const navigation = document.querySelector("#primary-nav");

menuToggle.addEventListener("click", () => {
    const isExpanded = menuToggle.getAttribute("aria-expanded") === "true";
    menuToggle.setAttribute("aria-expanded", String(!isExpanded));
    menuToggle.setAttribute("aria-label", isExpanded ? "Open navigation" : "Close navigation");
    navigation.classList.toggle("is-open", !isExpanded);
});

navigation.addEventListener("click", (event) => {
    if (event.target.closest("a")) {
        menuToggle.setAttribute("aria-expanded", "false");
        menuToggle.setAttribute("aria-label", "Open navigation");
        navigation.classList.remove("is-open");
    }
});

const joinForm = document.querySelector("#join-form");
const emailInput = document.querySelector("#email");
const formMessage = document.querySelector("#form-message");

joinForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!emailInput.checkValidity()) {
        emailInput.reportValidity();
        return;
    }

    formMessage.textContent = "Thanks for your interest! Email signup isn’t connected yet.";
    joinForm.reset();
});

const whatsappNumber = document.querySelector('meta[name="whatsapp-number"]').content;
const ideaForm = document.querySelector("#idea-form");
const chatLauncher = document.querySelector("#chat-launcher");
const chatPanel = document.querySelector("#chat-panel");
const chatClose = document.querySelector("#chat-close");
const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-input");
const chatMessages = document.querySelector("#chat-messages");
const chatWhatsapp = document.querySelector("#chat-whatsapp");
const conversation = [];
const sparkPrompts = [
    "Build a tiny tool that saves someone five minutes every day.",
    "Invent a friendlier way for beginners to learn one tech skill.",
    "What could you make with one free afternoon and a phone?",
    "Design a community app that makes people feel more welcome.",
    "Turn one annoying daily task into a one-click solution.",
    "Make a website that answers a question people are shy to ask.",
    "How could technology help neighbors share what they already have?",
    "Create a playful way to teach someone how the internet works.",
    "Imagine a helpful robot with absolutely no screen. What does it do?",
    "Make something useful from a spreadsheet, a form, and a good idea.",
    "What would a social network look like if it rewarded curiosity?",
    "Pick a local problem. Sketch a simple digital fix for it."
];
let previousSparkIndex = 0;
let sparkCount = 1;

function makeWhatsAppUrl(message) {
    return `https://wa.me/${whatsappNumber}?text=${encodeURIComponent(message)}`;
}

const sparkPrompt = document.querySelector("#spark-prompt");
const sparkCounter = document.querySelector("#spark-counter");
const sparkMachine = document.querySelector(".spark-machine");

document.querySelector("#spark-button").addEventListener("click", () => {
    let nextIndex = Math.floor(Math.random() * sparkPrompts.length);
    while (nextIndex === previousSparkIndex) {
        nextIndex = Math.floor(Math.random() * sparkPrompts.length);
    }

    previousSparkIndex = nextIndex;
    sparkCount += 1;
    sparkPrompt.textContent = sparkPrompts[nextIndex];
    sparkCounter.textContent = `SPARK ${String(sparkCount).padStart(2, "0")}`;
    sparkMachine.classList.remove("is-spinning");
    void sparkMachine.offsetWidth;
    sparkMachine.classList.add("is-spinning");
});

document.querySelector("#spark-share").addEventListener("click", () => {
    const message = `Hi TechTok Club! Here's a side quest I got from the website:\n\n"${sparkPrompt.textContent}"\n\nWhat do you think?`;
    window.open(makeWhatsAppUrl(message), "_blank", "noopener,noreferrer");
});

const donateDialog = document.querySelector("#donate-dialog");
const donateCopyStatus = document.querySelector("#donate-copy-status");

document.querySelector("#donate-open").addEventListener("click", () => {
    donateCopyStatus.textContent = "";
    donateDialog.showModal();
});

document.querySelector("#copy-till").addEventListener("click", async () => {
    try {
        await navigator.clipboard.writeText(document.querySelector("#till-number").textContent.trim());
        donateCopyStatus.textContent = "Till number copied.";
    } catch (error) {
        donateCopyStatus.textContent = "Copy unavailable here. Please enter Till number 6700644 in M-Pesa.";
    }
});

ideaForm.addEventListener("submit", (event) => {
    event.preventDefault();

    const ideaField = ideaForm.elements.idea;
    const idea = ideaField.value.trim();
    ideaField.setCustomValidity(idea ? "" : "Please enter an idea before sharing.");
    if (!ideaForm.reportValidity()) return;
    const name = ideaForm.elements.name.value.trim();
    const message = `Hi TechTok Club!${name ? ` I'm ${name}.` : ""}\n\nI'd like to share an idea:\n${idea}`;
    window.open(makeWhatsAppUrl(message), "_blank", "noopener,noreferrer");
});

chatLauncher.addEventListener("click", () => {
    const isOpen = chatLauncher.getAttribute("aria-expanded") === "true";
    chatLauncher.setAttribute("aria-expanded", String(!isOpen));
    chatPanel.hidden = isOpen;
    if (!isOpen) chatInput.focus();
});

chatClose.addEventListener("click", () => {
    chatPanel.hidden = true;
    chatLauncher.setAttribute("aria-expanded", "false");
    chatLauncher.focus();
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !chatPanel.hidden) {
        chatPanel.hidden = true;
        chatLauncher.setAttribute("aria-expanded", "false");
        chatLauncher.focus();
    }
});

function addChatMessage(text, role, busy = false) {
    const message = document.createElement("li");
    message.className = `chat-message ${role}-message`;
    message.textContent = text;
    if (busy) message.setAttribute("aria-busy", "true");
    chatMessages.append(message);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return message;
}

function updateWhatsAppHandoff() {
    const transcript = conversation.slice(-8)
        .map(({ role, content }) => `${role === "user" ? "Visitor" : "TechTok AI"}: ${content}`)
        .join("\n\n");
    chatWhatsapp.href = makeWhatsAppUrl(
        transcript
            ? `Hi TechTok Club! I was chatting with the website assistant and would like to continue:\n\n${transcript}`
            : "Hi TechTok Club! I'd like to chat with someone."
    );
}

chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const content = chatInput.value.trim();
    chatInput.setCustomValidity(content ? "" : "Please enter a message.");
    if (!chatForm.reportValidity()) return;

    conversation.push({ role: "user", content });
    addChatMessage(content, "user");
    chatInput.value = "";
    chatInput.disabled = true;
    const sendButton = chatForm.querySelector("button");
    sendButton.disabled = true;
    const pendingMessage = addChatMessage("Thinking…", "assistant", true);
    updateWhatsAppHandoff();

    try {
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ messages: conversation.slice(-10) })
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || "The AI assistant couldn’t answer right now.");
        }

        pendingMessage.remove();
        conversation.push({ role: "assistant", content: result.reply });
        addChatMessage(result.reply, "assistant");
        updateWhatsAppHandoff();
    } catch (error) {
        pendingMessage.remove();
        const message = error instanceof Error
            ? error.message
            : "The AI assistant couldn’t connect. Please try again or contact us on WhatsApp.";
        addChatMessage(message, "assistant");
    } finally {
        chatInput.disabled = false;
        sendButton.disabled = false;
        chatInput.focus();
    }
});