// Copy a rendered table to the clipboard as CSV. Wired up via an inline
// onclick in _table.html so it keeps working after HTMX swaps the table.
function copyTableCsv(btn) {
  const block = btn.closest(".table-block");
  const table = block && block.querySelector("table");
  if (!table) return;

  const esc = (text) => {
    const s = (text || "").trim();
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const csv = [...table.querySelectorAll("tr")]
    .map((tr) => [...tr.querySelectorAll("th,td")].map((c) => esc(c.textContent)).join(","))
    .join("\n");

  const flash = (msg) => {
    const original = btn.dataset.label || btn.textContent;
    btn.dataset.label = original;
    btn.textContent = msg;
    setTimeout(() => {
      btn.textContent = original;
    }, 1200);
  };

  const fallback = () => {
    // navigator.clipboard is unavailable outside secure contexts; use execCommand.
    const ta = document.createElement("textarea");
    ta.value = csv;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } finally {
      ta.remove();
    }
    flash(ok ? "Copied!" : "Copy failed");
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(csv).then(() => flash("Copied!"), fallback);
  } else {
    fallback();
  }
}
