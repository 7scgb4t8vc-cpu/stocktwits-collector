// Update refresh label with current time
function updateRefreshLabel() {
  const label = document.getElementById("refresh-label");
  if (label) {
    const now = new Date();
    label.textContent = "Updated " + now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
}
updateRefreshLabel();
setInterval(updateRefreshLabel, 60000);
