# Советник прямо в настоящем кабинете — для видео

Закладка вставляет наш блок советов в открытую страницу личного кабинета магазина у вас в браузере: находит блок «Популярные решения» (или «Вам может подойти»), прячет его и ставит на его место наш — с объяснениями, «Запросить счёт» и ссылками. Ничего не отправляет на сайт магазина и ничего не меняет у него: это только вид страницы у вас на экране, обновление страницы всё возвращает. Советы считает тот же движок, что на открытом стенде (виджет грузится с https://matbcontrol.github.io/sovetnik-lk/), для тестового клиента кабинета — по его четырём покупкам.

Как поставить:
1. Создайте в браузере новую закладку (Ctrl+D на любой странице → «Изменить»).
2. Название — «Советник», в поле адреса вставьте строку ниже целиком.
3. Войдите в кабинет под тестовым клиентом, откройте «Главную» (там блок «Популярные решения») и нажмите закладку.

```
javascript:(()=>{const P=[{family:'kaspersky/kaspersky-endpoint-security-dlya-biznesa-standartnyiy',date:'2026-05-11',term:12},{family:'adobe/adobe-creative-cloud',date:'2025-11-23',term:12},{family:'drweb/drweb-desktop-security-suite',date:'2026-05-11',term:12},{family:'microsoft/microsoft-365-personal',date:'2026-04-30',term:12}];const d=document;if(d.getElementById('sovetnik'))return;let h=null;for(const e of d.body.querySelectorAll('*')){if(!/^(SCRIPT|STYLE)$/.test(e.tagName)&&/Вам может подойти|Популярные решения/.test(e.textContent))h=e;}const box=d.createElement('div');box.id='sovetnik';box.style.cssText='margin:16px 0';if(h){let c=h;while(c.parentElement&&c.parentElement!==d.body&&c.textContent.trim().length<300)c=c.parentElement;c.parentNode.insertBefore(box,c);c.style.display='none';}else{d.body.prepend(box);}const go=()=>Sovetnik.mount('#sovetnik',{type:'b2b',today:new Date().toISOString().slice(0,10),client_id:'demo',purchases:P});if(window.Sovetnik){go();return;}const s=d.createElement('script');s.src='https://matbcontrol.github.io/sovetnik-lk/widget.js';s.onload=go;d.head.appendChild(s);})();
```

Если такого блока на странице нет, наш блок встанет в начало страницы. Вернуть как было — обновить страницу.

Для видео: сцена 0:00–0:20 — «Главная» кабинета с «Популярными решениями», затем нажать закладку — на том же месте появляются личные советы.
