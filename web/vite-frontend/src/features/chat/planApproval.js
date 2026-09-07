export function createPlanApprovalRenderer({ sendMessage, scrollToBottom }) {
  return function addPlanApprovalActions(messageView) {
    const agentMessage = messageView?.element || messageView?.closest?.(".agent-message");
    if (!agentMessage || agentMessage.nextElementSibling?.classList.contains("plan-approval-message")) {
      return null;
    }

    const responseMessage = document.createElement("div");
    responseMessage.className = "message user-message plan-approval-message is-entering";
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const prompt = document.createElement("div");
    prompt.className = "plan-approval-prompt";
    prompt.textContent = "How would you like to proceed?";
    const actions = document.createElement("div");
    actions.className = "plan-approval-actions";
    actions.setAttribute("role", "group");
    actions.setAttribute("aria-label", "Plan actions");

    const disableControls = () => {
      responseMessage.querySelectorAll("button, input").forEach((item) => {
        item.disabled = true;
      });
    };
    [
      ["yes", "Approve plan", "Approve this plan and start execution", "is-approve"],
      ["replan", "Revise plan", "Ask the agent to revise this plan", "is-replan"],
    ].forEach(([message, label, title, variant]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `plan-approval-btn ${variant}`;
      button.textContent = label;
      button.title = title;
      button.addEventListener("click", () => {
        disableControls();
        sendMessage(message);
      });
      actions.appendChild(button);
    });

    const feedback = document.createElement("div");
    feedback.className = "plan-feedback";
    const feedbackLabel = document.createElement("label");
    feedbackLabel.className = "plan-feedback-label";
    feedbackLabel.textContent = "Or describe what you’d like changed";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "plan-feedback-input";
    input.placeholder = "Other feedback or changes…";
    input.setAttribute("aria-label", "Other feedback about this plan");
    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "plan-approval-btn plan-feedback-submit";
    submit.textContent = "Send";
    submit.title = "Send feedback about this plan";
    submit.disabled = true;
    const sendFeedback = () => {
      const message = input.value.trim();
      if (!message) return;
      disableControls();
      sendMessage(message);
    };
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      sendFeedback();
    });
    input.addEventListener("input", () => {
      submit.disabled = !input.value.trim();
    });
    submit.addEventListener("click", sendFeedback);
    feedbackLabel.appendChild(input);
    feedback.append(feedbackLabel, submit);
    bubble.append(prompt, actions, feedback);
    responseMessage.appendChild(bubble);
    agentMessage.after(responseMessage);
    scrollToBottom({ preserveUserPosition: true });
    return responseMessage;
  };
}
