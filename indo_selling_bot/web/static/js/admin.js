/**
 * Admin Panel Vanilla JavaScript
 * Modal handling, responsive mobile menu, auto-dismissing alerts.
 */

document.addEventListener("DOMContentLoaded", function () {
  // Mobile sidebar toggle
  const mobileToggle = document.getElementById("mobileToggle");
  const sidebar = document.getElementById("sidebar");

  if (mobileToggle && sidebar) {
    mobileToggle.addEventListener("click", function () {
      sidebar.classList.toggle("open");
    });

    // Close when clicking outside on mobile
    document.addEventListener("click", function (e) {
      if (!sidebar.contains(e.target) && !mobileToggle.contains(e.target) && sidebar.classList.contains("open")) {
        sidebar.classList.remove("open");
      }
    });
  }

  // Modal Open/Close helpers
  window.openModal = function (modalId) {
    const el = document.getElementById(modalId);
    if (el) el.classList.add("open");
  };

  window.closeModal = function (modalId) {
    const el = document.getElementById(modalId);
    if (el) el.classList.remove("open");
  };

  // Close modal when backdrop clicked
  document.querySelectorAll(".modal-backdrop").forEach(function (modal) {
    modal.addEventListener("click", function (e) {
      if (e.target === modal) {
        modal.classList.remove("open");
      }
    });
  });

  // Auto-dismiss alert messages after 6 seconds
  document.querySelectorAll(".alert-box").forEach(function (alert) {
    setTimeout(function () {
      alert.style.transition = "opacity 0.5s ease";
      alert.style.opacity = "0";
      setTimeout(function () {
        alert.remove();
      }, 500);
    }, 6000);
  });
});
