# Советник прямо в настоящем кабинете — для видео

Закладка вставляет наш блок советов в открытую страницу личного кабинета магазина у вас в браузере: находит магазинный блок «Вам может подойти» (а если его нет — «Популярные решения»), прячет его и ставит на его место наш — с объяснениями, «Запросить счёт» и ссылками. Ничего не отправляет на сайт магазина и ничего не меняет у него: это только вид страницы у вас на экране, обновление страницы всё возвращает. Советы считает тот же движок, что на открытом стенде (виджет грузится с https://matbcontrol.github.io/sovetnik-lk/), для тестового клиента кабинета — по его четырём покупкам.

Как поставить:
1. Создайте в браузере новую закладку (Ctrl+D на любой странице → «Изменить»).
2. Название — «Советник», в поле адреса вставьте строку ниже целиком.
3. Войдите в кабинет под тестовым клиентом, откройте «Главную» (под акциями — блок «Вам может подойти») и нажмите закладку. Если блоки страницы ещё грузятся, закладка ждёт их до 10 секунд.

```
javascript:(()=>{const P=[{family:'kaspersky/kaspersky-endpoint-security-dlya-biznesa-standartnyiy',date:'2026-05-11',term:12},{family:'adobe/adobe-creative-cloud',date:'2025-11-23',term:12},{family:'drweb/drweb-desktop-security-suite',date:'2026-05-11',term:12},{family:'microsoft/microsoft-365-personal',date:'2026-04-30',term:12}];const d=document;if(window.__sovetnik||d.getElementById('sovetnik'))return;window.__sovetnik=1;const find=(re)=>{for(const e of d.body.querySelectorAll('h1,h2,h3,h4,div,span,p')){if(!e.children.length&&re.test(e.textContent.trim()))return e;}return null;};let n=0;const put=()=>{const h=find(/^Вам может подойти$/)||find(/^Популярные решения$/);if(!h&&++n<20)return setTimeout(put,500);const box=d.createElement('div');box.id='sovetnik';box.style.cssText='margin:16px 0';if(h){let c=h;while(c.parentElement&&c.parentElement!==d.body&&c.textContent.trim().length<300)c=c.parentElement;c.parentNode.insertBefore(box,c);c.style.display='none';}else{d.body.prepend(box);}const go=()=>Sovetnik.mount('#sovetnik',{type:'b2b',today:new Date().toISOString().slice(0,10),client_id:'demo',purchases:P});if(window.Sovetnik){go();return;}const s=d.createElement('script');s.src='https://matbcontrol.github.io/sovetnik-lk/widget.js';s.onload=go;d.head.appendChild(s);};put();})();
```

Если за 10 секунд ни одного из этих блоков не появилось, наш блок встанет в начало страницы. Вернуть как было — обновить страницу.

Проверено 28 ноября в кабинете тестового клиента: наш блок встаёт сразу под акциями, на место магазинного «Вам может подойти»; «Популярные решения» ниже остаются как были.

Для видео: сцена 0:00–0:20 — «Главная» кабинета, под акциями магазинный «Вам может подойти» с общими карточками; нажать закладку — на том же месте появляются личные советы с объяснениями.
