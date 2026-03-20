document.addEventListener("DOMContentLoaded", () => {
    const liveTime = document.querySelector("[data-live-time]");
    if (liveTime) {
        const updateTime = () => {
            const now = new Date();
            liveTime.textContent = now.toLocaleString();
        };

        updateTime();
        window.setInterval(updateTime, 1000);
    }

    const searchInput = document.querySelector("[data-attendance-search]");
    if (searchInput) {
        const rows = Array.from(document.querySelectorAll("[data-attendance-row]"));
        const emptyState = document.querySelector("[data-filter-empty]");

        const applyFilter = () => {
            const query = searchInput.value.trim().toLowerCase();
            let visibleRows = 0;

            rows.forEach((row) => {
                const haystack = (row.dataset.search || "").toLowerCase();
                const isVisible = haystack.includes(query);
                row.hidden = !isVisible;
                if (isVisible) {
                    visibleRows += 1;
                }
            });

            if (emptyState) {
                emptyState.hidden = visibleRows !== 0;
            }
        };

        searchInput.addEventListener("input", applyFilter);
        applyFilter();
    }

    const imageInput = document.querySelector("[data-image-input]");
    if (imageInput) {
        const fileName = document.querySelector("[data-file-name]");
        const imagePreview = document.querySelector("[data-image-preview]");
        const placeholder = document.querySelector("[data-preview-placeholder]");

        imageInput.addEventListener("change", () => {
            const file = imageInput.files && imageInput.files[0];

            if (fileName) {
                fileName.textContent = file ? file.name : "No image selected";
            }

            if (!file || !imagePreview) {
                if (placeholder) {
                    placeholder.hidden = false;
                }
                if (imagePreview) {
                    imagePreview.hidden = true;
                    imagePreview.removeAttribute("src");
                }
                return;
            }

            const objectUrl = URL.createObjectURL(file);
            imagePreview.src = objectUrl;
            imagePreview.hidden = false;
            if (placeholder) {
                placeholder.hidden = true;
            }
        });
    }

    document.querySelectorAll("[data-confirm]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            const message = form.getAttribute("data-confirm") || "Are you sure?";
            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });
});
