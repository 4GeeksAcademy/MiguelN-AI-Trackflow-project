(function () {
  var form = document.getElementById("signup-form");
  if (!form) {
    return;
  }

  var emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  var minPasswordLength = 8;

  var messageMap = {
    workEmail: {
      empty: "Please enter your work email so we can contact you.",
      invalid: "Please enter a valid email format, for example: name@company.com."
    },
    securityCode: {
      empty: "Please create a security code to protect your account.",
      weakLength: "Please use at least 8 characters in your security code.",
      weakUpper: "Please add at least one uppercase letter (A-Z).",
      weakLower: "Please add at least one lowercase letter (a-z)."
    },
    consent: {
      empty: "Please confirm consent so we can continue with your request."
    }
  };

  var successMap = {
    workEmail: "Email format looks valid.",
    securityCode: "Security code strength requirements are met.",
    consent: "Thank you for confirming consent."
  };

  var fields = Array.prototype.slice.call(form.querySelectorAll("input, select, textarea"));

  fields.forEach(function (field) {
    attachFeedback(field);

    field.addEventListener("blur", function () {
      field.dataset.interacted = "true";
      validateField(field, true);
    });

    field.addEventListener("input", function () {
      if (field.dataset.interacted === "true") {
        validateField(field, true);
      }
    });

    field.addEventListener("change", function () {
      if (field.dataset.interacted === "true") {
        validateField(field, true);
      }
    });
  });

  form.addEventListener("submit", function (event) {
    var invalidField = null;

    fields.forEach(function (field) {
      field.dataset.interacted = "true";
      var isValid = validateField(field, true);
      if (!isValid && !invalidField) {
        invalidField = field;
      }
    });

    if (invalidField) {
      event.preventDefault();
      invalidField.focus();
    }
  });

  function attachFeedback(field) {
    if (field.type === "hidden") {
      return;
    }

    if (getFeedbackNode(field)) {
      return;
    }

    var feedback = document.createElement("p");
    feedback.className = "mt-2 hidden text-[0.8rem] font-semibold leading-[1.3]";
    feedback.id = "feedback-" + field.id;
    feedback.setAttribute("aria-live", "polite");

    var describedBy = field.getAttribute("aria-describedby");
    var describedByIds = describedBy ? describedBy.split(/\s+/).filter(Boolean) : [];
    if (describedByIds.indexOf(feedback.id) === -1) {
      describedByIds.push(feedback.id);
      field.setAttribute("aria-describedby", describedByIds.join(" "));
    }

    var mountNode = field;
    if (field.type === "checkbox") {
      mountNode = field.closest("label") || field;
    }

    mountNode.insertAdjacentElement("afterend", feedback);
  }

  function getFeedbackNode(field) {
    return document.getElementById("feedback-" + field.id);
  }

  function validateField(field, showFeedback) {
    if (field.disabled || field.type === "hidden") {
      return true;
    }

    var value = field.type === "checkbox" ? field.checked : field.value.trim();
    var isValid = true;
    var message = successMap[field.id] || "Looks good.";

    if (field.required) {
      if (field.type === "checkbox") {
        if (!value) {
          isValid = false;
          message = getMessage(field.id, "empty", "Please complete this field to continue.");
        }
      } else if (!value) {
        isValid = false;
        message = getMessage(field.id, "empty", "Please complete this field to continue.");
      }
    }

    if (isValid && field.id === "workEmail") {
      if (!emailPattern.test(String(value))) {
        isValid = false;
        message = getMessage(field.id, "invalid", "Please enter a valid email address.");
      }
    }

    if (isValid && field.id === "securityCode") {
      if (value.length < minPasswordLength) {
        isValid = false;
        message = getMessage(field.id, "weakLength", "Please use at least 8 characters.");
      } else if (!/[A-Z]/.test(value)) {
        isValid = false;
        message = getMessage(field.id, "weakUpper", "Please include at least one uppercase letter.");
      } else if (!/[a-z]/.test(value)) {
        isValid = false;
        message = getMessage(field.id, "weakLower", "Please include at least one lowercase letter.");
      }
    }

    if (showFeedback) {
      updateUI(field, isValid, message);
    }

    return isValid;
  }

  function updateUI(field, isValid, message) {
    var feedback = getFeedbackNode(field);
    if (!feedback) {
      return;
    }

    field.classList.remove(
      "border-red-600",
      "ring-4",
      "ring-red-600/15",
      "border-green-600",
      "ring-green-600/15",
      "dark:border-red-400",
      "dark:ring-red-400/20",
      "dark:border-green-400",
      "dark:ring-green-400/20"
    );
    feedback.classList.remove(
      "hidden",
      "flex",
      "text-red-700",
      "text-green-700",
      "dark:text-red-300",
      "dark:text-green-300"
    );

    if (isValid) {
      field.classList.add("border-green-600", "ring-4", "ring-green-600/15", "dark:border-green-400", "dark:ring-green-400/20");
      feedback.classList.add("flex", "text-green-700", "dark:text-green-300");
      feedback.textContent = "✓ " + message;
      field.setAttribute("aria-invalid", "false");
      return;
    }

    field.classList.add("border-red-600", "ring-4", "ring-red-600/15", "dark:border-red-400", "dark:ring-red-400/20");
    feedback.classList.add("flex", "text-red-700", "dark:text-red-300");
    feedback.textContent = "⚠ " + message;
    field.setAttribute("aria-invalid", "true");
  }

  function getMessage(fieldId, key, fallback) {
    var fieldMessages = messageMap[fieldId] || {};
    return fieldMessages[key] || fallback;
  }
})();
