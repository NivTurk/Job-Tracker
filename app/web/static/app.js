// Small client-side helpers. Everything else is server-rendered + htmx.

// After "Fetch text" returns, fill title / company / location from the
// posting's structured data, but only where the field is still empty.
document.addEventListener("htmx:afterSettle", () => {
  const source = document.querySelector("#jd-field .detected:not([data-applied])");
  if (!source) return;
  source.dataset.applied = "1";
  for (const field of ["title", "company", "location"]) {
    const value = source.dataset[field];
    const input = document.getElementById(field);
    if (value && input && !input.value.trim()) {
      input.value = value;
      // Lets the duplicate check run for the filled-in company/title.
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }
});
