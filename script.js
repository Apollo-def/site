// Dados dos cursos
const cursos = [
  {
    titulo: "Administração",
    descricao: "Curso completo de Administração com foco em gestão empresarial, liderança e empreendedorismo.",
    imagem: "img/03.jpg",
    link: "#"
  },
  {
    titulo: "Comunicação",
    descricao: "Aprenda técnicas avançadas de comunicação eficaz para o mercado de trabalho e mídias digitais.",
    imagem: "img/comunicacao.jpg",
    link: "#"
  },
  {
    titulo: "Inglês",
    descricao: "Domine o idioma inglês com professores nativos e metodologias modernas para oportunidades globais.",
    imagem: "img/ingles.jpg",
    link: "#"
  },
  {
    titulo: "Sustentabilidade",
    descricao: "Cursos sobre práticas sustentáveis, responsabilidade ambiental e desenvolvimento sustentável.",
    imagem: "img/sustentavel.jpg",
    link: "#"
  }
];

// Renderizar cards
function renderizarCards(filtrados = cursos) {
  const container = document.getElementById('cards-container');
  container.innerHTML = '';

  filtrados.forEach((curso, index) => {
    const card = document.createElement('div');
    card.className = 'card';

    card.innerHTML = `
      <img src="${curso.imagem}" alt="${curso.titulo}">
      <div class="card-content">
        <h3>${curso.titulo}</h3>
        <p>${curso.descricao}</p>
        <button class="ver-mais-btn" data-index="${index}">Ver Mais</button>
      </div>
    `;

    container.appendChild(card);
  });
}

// Busca com debounce
let debounceTimer;
function buscarCursos() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    const termo = document.getElementById('busca-curso').value.toLowerCase();
    const filtrados = cursos.filter(curso =>
      curso.titulo.toLowerCase().includes(termo) ||
      curso.descricao.toLowerCase().includes(termo)
    );
    renderizarCards(filtrados);
  }, 300);
}

// Modal para detalhes do curso
function abrirModalCurso(index) {
  const curso = cursos[index];
  document.getElementById('modal-title').textContent = curso.titulo;
  document.getElementById('modal-desc').textContent = curso.descricao;
  document.getElementById('course-modal').style.display = 'block';
}

// Modal para adicionar curso
function abrirModalAdd() {
  document.getElementById('add-modal').style.display = 'block';
}

// Fechar modais
function fecharModal() {
  document.getElementById('course-modal').style.display = 'none';
  document.getElementById('add-modal').style.display = 'none';
}

// Adicionar novo curso
function adicionarCurso(event) {
  event.preventDefault();
  const form = event.target;
  const titulo = form[0].value;
  const descricao = form[1].value;
  const imagem = form[2].value;

  cursos.push({ titulo, descricao, imagem, link: "#" });
  renderizarCards();
  fecharModal();
  form.reset();
}

// Smooth scroll para navegação
function smoothScroll(target) {
  const element = document.querySelector(target);
  if (element) {
    element.scrollIntoView({ behavior: 'smooth' });
  }
}

// Menu mobile
function toggleMobileMenu() {
  const nav = document.querySelector('.nav ul');
  nav.style.display = nav.style.display === 'flex' ? 'none' : 'flex';
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
  renderizarCards();

  document.getElementById('busca-curso').addEventListener('input', buscarCursos);

  document.getElementById('add-curso-btn').addEventListener('click', abrirModalAdd);

  document.getElementById('saiba-mais-btn').addEventListener('click', () => {
    smoothScroll('#sobre');
  });

  // Delegação de eventos para botões "Ver Mais"
  document.addEventListener('click', (e) => {
    if (e.target.classList.contains('ver-mais-btn')) {
      const index = e.target.getAttribute('data-index');
      abrirModalCurso(index);
    }
  });

  // Fechar modais
  document.querySelectorAll('.close').forEach(closeBtn => {
    closeBtn.addEventListener('click', fecharModal);
  });

  window.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal')) {
      fecharModal();
    }
  });

  // Formulário adicionar curso
  document.getElementById('add-form').addEventListener('submit', adicionarCurso);

  // Navegação
  document.querySelectorAll('.nav a').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const target = link.getAttribute('href');
      smoothScroll(target);
    });
  });

  // Menu mobile
  document.querySelector('.mobile-menu').addEventListener('click', toggleMobileMenu);
});
