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

  // Form handlers
  const contatoForm = document.getElementById('contato-form');
  if (contatoForm) {
    contatoForm.addEventListener('submit', (e) => {
      e.preventDefault();
      alert('Mensagem enviada com sucesso! Obrigado pelo contato.');
      contatoForm.reset();
    });
  }

  const denunciaForm = document.getElementById('denuncia-form');
  if (denunciaForm) {
    denunciaForm.addEventListener('submit', (e) => {
      e.preventDefault();
      alert('Denúncia enviada com sucesso! Sua mensagem será analisada de forma confidencial.');
      denunciaForm.reset();
    });
  }

  const stakeholderForm = document.getElementById('stakeholder-form');
  if (stakeholderForm) {
    stakeholderForm.addEventListener('submit', (e) => {
      e.preventDefault();
      alert('Mensagem enviada com sucesso! Responderemos em breve.');
      stakeholderForm.reset();
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
});
