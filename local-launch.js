// O banco e a autenticação dependem do servidor, não de file://.
if (location.protocol === 'file:') {
  const page = location.pathname.includes('/campo/') ? '/campo/index.html' : '/';
  location.replace(`http://127.0.0.1:8080${page}${location.search}`);
}
