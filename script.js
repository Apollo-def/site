// Menu mobile (global)
function toggleMobileMenu() {
  const nav = document.querySelector('.nav ul');
  if (nav) {
    nav.classList.toggle('active');
  }
}

// Smooth scrolling for navigation links
function smoothScrollToSection(targetId) {
  const targetSection = document.getElementById(targetId);
  if (targetSection) {
    const headerHeight = document.querySelector('.header').offsetHeight;
    const targetPosition = targetSection.offsetTop - headerHeight;
    window.scrollTo({
      top: targetPosition,
      behavior: 'smooth'
    });
  }
}

// Intersection Observer for animations
function observeSections() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('animate-in');
      }
    });
  }, { threshold: 0.1 });

  document.querySelectorAll('.sobre-item, .valor-item, .info-item, .conclusao-item, .contato-item').forEach(item => {
    observer.observe(item);
  });
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
  // Smooth scrolling navigation
  document.querySelectorAll('.nav a').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const href = link.getAttribute('href');
      if (href.startsWith('#')) {
        const targetId = href.substring(1);
        smoothScrollToSection(targetId);
        // Close mobile menu after click
        const navUl = document.querySelector('.nav ul');
        if (navUl.classList.contains('active')) {
          navUl.classList.remove('active');
        }
      }
    });
  });

  // Saiba Mais button - scroll to sobre section
  const saibaBtn = document.getElementById('saiba-mais-btn');
  if (saibaBtn) {
    saibaBtn.addEventListener('click', () => {
      smoothScrollToSection('sobre');
    });
  }



  // Menu mobile
  const mobileMenu = document.querySelector('.mobile-menu');
  if (mobileMenu) {
    mobileMenu.addEventListener('click', toggleMobileMenu);
  }

  // Close mobile menu on resize
  window.addEventListener('resize', () => {
    const navUl = document.querySelector('.nav ul');
    if (window.innerWidth > 768 && navUl) {
      navUl.classList.remove('active');
    }
  });

  // Initialize animations
  observeSections();

  // Add scroll effect to header
  window.addEventListener('scroll', () => {
    const header = document.querySelector('.header');
    if (window.scrollY > 100) {
      header.classList.add('scrolled');
    } else {
      header.classList.remove('scrolled');
    }
  });

  // Back to Top Button
  const backToTopBtn = document.getElementById('back-to-top');
  if (backToTopBtn) {
    window.addEventListener('scroll', () => {
      if (window.scrollY > 300) {
        backToTopBtn.classList.add('show');
      } else {
        backToTopBtn.classList.remove('show');
      }
    });

    backToTopBtn.addEventListener('click', () => {
      window.scrollTo({
        top: 0,
        behavior: 'smooth'
      });
    });
  }

  // Enhanced Form Validation and Loading States
  function showLoading(button) {
    button.classList.add('loading');
    button.disabled = true;
  }

  function hideLoading(button) {
    button.classList.remove('loading');
    button.disabled = false;
  }

  function validateForm(form) {
    const inputs = form.querySelectorAll('input[required], textarea[required], select[required]');
    let isValid = true;

    inputs.forEach(input => {
      if (!input.value.trim()) {
        input.style.borderColor = '#e74c3c';
        isValid = false;
      } else {
        input.style.borderColor = '#ddd';
      }
    });

    return isValid;
  }

  // Enhanced form handlers with validation and loading
  const contatoForm = document.getElementById('contato-form');
  if (contatoForm) {
    contatoForm.addEventListener('submit', async (e) => {
      e.preventDefault();

      if (!validateForm(contatoForm)) {
        alert('Por favor, preencha todos os campos obrigatórios.');
        return;
      }

      const submitBtn = contatoForm.querySelector('button[type="submit"]');
      showLoading(submitBtn);

      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 2000));

      hideLoading(submitBtn);
      alert('Mensagem enviada com sucesso! Obrigado pelo contato.');
      contatoForm.reset();
    });
  }

  const denunciaForm = document.getElementById('denuncia-form');
  if (denunciaForm) {
    denunciaForm.addEventListener('submit', async (e) => {
      e.preventDefault();

      if (!validateForm(denunciaForm)) {
        alert('Por favor, preencha todos os campos obrigatórios.');
        return;
      }

      const submitBtn = denunciaForm.querySelector('button[type="submit"]');
      showLoading(submitBtn);

      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 2000));

      hideLoading(submitBtn);
      alert('Denúncia enviada com sucesso! Sua mensagem será analisada de forma confidencial.');
      denunciaForm.reset();
    });
  }

  const stakeholderForm = document.getElementById('stakeholder-form');
  if (stakeholderForm) {
    stakeholderForm.addEventListener('submit', async (e) => {
      e.preventDefault();

      if (!validateForm(stakeholderForm)) {
        alert('Por favor, preencha todos os campos obrigatórios.');
        return;
      }

      const submitBtn = stakeholderForm.querySelector('button[type="submit"]');
      showLoading(submitBtn);

      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 2000));

      hideLoading(submitBtn);
      alert('Mensagem enviada com sucesso! Responderemos em breve.');
      stakeholderForm.reset();
    });
  }

  // Real-time form validation feedback
  document.querySelectorAll('input[required], textarea[required], select[required]').forEach(input => {
    input.addEventListener('blur', () => {
      if (input.value.trim()) {
        input.style.borderColor = '#27ae60';
      } else {
        input.style.borderColor = '#e74c3c';
      }
    });

    input.addEventListener('input', () => {
      if (input.value.trim()) {
        input.style.borderColor = '#27ae60';
      }
    });
  });
});
