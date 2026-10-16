/* =========================================================
   SORTIRIA
   Navigation et interactions légères
   ========================================================= */


/* Scroll fluide vers les ancres de la page */

const anchorLinks = document.querySelectorAll('a[href^="#"]');

anchorLinks.forEach(link => {
    link.addEventListener("click", event => {
        const href = link.getAttribute("href");

        if (!href || href === "#") return;

        const target = document.querySelector(href);

        if (!target) return;

        event.preventDefault();

        target.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    });
});


/* Page active dans la navigation */

const currentPage =
    window.location.pathname.split("/").pop() || "index.html";

const navLinks = document.querySelectorAll(".nav-links a");

navLinks.forEach(link => {
    const href = link.getAttribute("href");

    if (!href) return;

    const linkPage = href.split("/").pop();

    if (linkPage === currentPage) {
        link.classList.add("active");
    }
});