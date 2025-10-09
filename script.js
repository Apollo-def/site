// 🔗 Elementos da interface
const cardsContainer = document.getElementById('cards-container');
const saibaMaisBtn = document.getElementById('saiba-mais-btn');
const mainTitle = document.getElementById('main-title');
const addProdutoBtn = document.getElementById('add-produto-btn');
const buscaInput = document.getElementById('busca-curso');

// 🌙 Botão modo escuro (adicionado dinamicamente)
const modoEscuroBtn = document.createElement('button');
modoEscuroBtn.id = 'modo-escuro-btn';
modoEscuroBtn.textContent = 'Alternar Tema';
document.querySelector('.intro').appendChild(modoEscuroBtn);



// 🧩 Cursos locais salvos no projeto
const cursosLocais = [
  {
    titulo: "Curso de Gastronomia",
    descricao: "Aprenda técnicas culinárias com chefs renomados.",
    imagem: "img/03.png"
  },
  {
    titulo: "Curso de Moda e Design",
    descricao: "Desenvolva coleções e aprenda sobre tendências.",
    imagem: "img/04.png"
  },
  {
    titulo: "Curso de Tecnologia",
    descricao: "Domine ferramentas digitais e programação.",
    imagem: "img/03.jpg"
  }
];

// ➕ Cursos extras para adicionar manualmente
const cursosExtras = [
  {
    titulo: "Curso de Fotografia Criativa",
    descricao: "Capture momentos com técnica e arte.",
    imagem: "img/foto.jpg"
  },
  {
    titulo: "Curso de Comunicação Corporativa",
    descricao: "Aprenda a se comunicar com impacto no ambiente profissional.",
    imagem: "img/comunicacao.jpg"
  },
  {
    titulo: "Curso de Ciências Naturais e Sustentabilidade",
    descricao: "Explore o meio ambiente e práticas sustentáveis.",
    imagem: "img/sustentavel.jpg"
  }
];

// 🧱 Cria card de curso
function criarCard(curso) {
  const card = document.createElement('div');
  card.classList.add('card', 'fade-in'); // 🧩 animação de entrada
  card.innerHTML = `
    <img src="${curso.imagem}" alt="${curso.titulo}">
    <p><strong>${curso.titulo}</strong></p>
    <p>${curso.descricao || ''}</p>
    <button class="ver-mais-btn">Ver Mais</button>
  `;
  // 2️⃣ Exibe nome do produto ao clicar
  card.querySelector('.ver-mais-btn').addEventListener('click', () => {
    alert(`Curso: ${curso.titulo}`);
  });
  cardsContainer.appendChild(card);
}

// 🔄 Renderiza cursos locais
function carregarCursosLocais() {
  cursosLocais.forEach(curso => criarCard(curso));
}

// 🔄 Carrega cursos da API (opcional)
async function carregarCursosDaAPI() {
  try {
    const resposta = await fetch(API_URL);
    const cursos = await resposta.json();
    cursos.forEach(curso => {
      criarCard({
        titulo: curso.title,
        descricao: "Curso externo",
        imagem: curso.thumbnailUrl
      });
    });
  } catch (erro) {
    console.error('Erro ao carregar cursos da API:', erro);
  }
}

// 1️⃣ Interação com botão "Saiba Mais"
saibaMaisBtn.addEventListener('click', () => {
  mainTitle.textContent = 'Explore os cursos incríveis do Senac!';
  mainTitle.style.color = '#f7a300';
  saibaMaisBtn.textContent = 'Explorar';
});

// 3️⃣ Adiciona curso aleatório ao clicar
addProdutoBtn.addEventListener('click', () => {
  const cursoAleatorio = cursosExtras[Math.floor(Math.random() * cursosExtras.length)];
  criarCard(cursoAleatorio);
});

// 🔍 Busca por curso
buscaInput.addEventListener('input', (e) => {
  const termo = e.target.value.toLowerCase();
  const cards = document.querySelectorAll('.card');
  cards.forEach(card => {
    const titulo = card.querySelector('p').textContent.toLowerCase();
    card.style.display = titulo.includes(termo) ? 'block' : 'none';
  });
});

// 4️⃣ Modo escuro com persistência
function aplicarTemaSalvo() {
  const tema = localStorage.getItem('tema');
  if (tema === 'escuro') {
    document.body.classList.add('dark-mode');
  }
}

modoEscuroBtn.addEventListener('click', () => {
  document.body.classList.toggle('dark-mode');
  const temaAtual = document.body.classList.contains('dark-mode') ? 'escuro' : 'claro';
  localStorage.setItem('tema', temaAtual);
});

// 🚀 Inicializa tudo ao carregar a página
window.addEventListener('DOMContentLoaded', () => {
  aplicarTemaSalvo();
  carregarCursosLocais();
  carregarCursosDaAPI();
});
