# --- admin_handlers.py (VERSÃO CORRIGIDA E COMPLETA) ---

import os
import logging
import asyncio
from functools import wraps
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter

import db_supabase as db
import scheduler
from utils import send_access_links, format_date_br

logger = logging.getLogger(__name__)

# --- Carrega IDs de Admin do .env ---
ADMIN_IDS_STR = os.getenv("ADMIN_USER_IDS", "")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',')] if ADMIN_IDS_STR else []

# --- IDs dos produtos ---
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))

# --- Estados da ConversationHandler ---
(
    SELECTING_ACTION,
    GETTING_USER_ID_FOR_CHECK,
    GETTING_USER_ID_FOR_GRANT,
    SELECTING_PLAN_FOR_GRANT,
    GETTING_USER_ID_FOR_REVOKE,
    CONFIRMING_REVOKE,
    GETTING_BROADCAST_MESSAGE,
    CONFIRMING_BROADCAST,
    SELECTING_NEW_GROUP,
    CONFIRMING_NEW_GROUP_BROADCAST,
    # NOVOS ESTADOS
    VIEWING_STATS,
    MANAGING_GROUPS,
    GETTING_GROUP_ACTION,
    GETTING_GROUP_NAME,
    GETTING_GROUP_ID,
    VIEWING_LOGS,
    CREATING_COUPON,
    GETTING_COUPON_CODE,
    GETTING_COUPON_DISCOUNT,
    SEARCHING_TRANSACTIONS,
    GETTING_TRANSACTION_SEARCH,
) = range(23)

# --- DECORATOR DE SEGURANÇA ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            logger.warning(f"Acesso não autorizado ao painel admin pelo usuário {user_id}.")
            if update.message:
                await update.message.reply_text("Você não tem permissão para usar este comando.")
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- FUNÇÃO AUXILIAR PARA O MENU PRINCIPAL ---
async def show_main_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False):
    """Mostra o painel de administração principal aprimorado."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Estatísticas", callback_data="admin_stats"),
            InlineKeyboardButton("🔍 Checar Usuário", callback_data="admin_check_user")
        ],
        [
            InlineKeyboardButton("✅ Conceder Acesso", callback_data="admin_grant_access"),
            InlineKeyboardButton("❌ Revogar Acesso", callback_data="admin_revoke_access")
        ],
        [
            InlineKeyboardButton("📢 Mensagem Global", callback_data="admin_broadcast"),
            InlineKeyboardButton("✉️ Link Novo Grupo", callback_data="admin_grant_new_group")
        ],
        [
            InlineKeyboardButton("🏢 Gerenciar Grupos", callback_data="admin_manage_groups"),
            InlineKeyboardButton("🎟️ Criar Cupom", callback_data="admin_create_coupon")
        ],
        [
            InlineKeyboardButton("📝 Ver Logs", callback_data="admin_view_logs"),
            InlineKeyboardButton("💳 Transações", callback_data="admin_transactions")
        ],
        [InlineKeyboardButton("✖️ Fechar Painel", callback_data="admin_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "👑 *Painel de Administração Avançado*\n\nSelecione uma ação:"

    if is_edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# --- HANDLER PRINCIPAL ---
@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ponto de entrada para o /admin."""
    await show_main_admin_menu(update, context)
    return SELECTING_ACTION

@admin_only
async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback para o botão Voltar."""
    query = update.callback_query
    await query.answer()
    await show_main_admin_menu(update, context, is_edit=True)
    return SELECTING_ACTION

# --- NOVA FUNCIONALIDADE: ESTATÍSTICAS ---
@admin_only
async def view_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra estatísticas detalhadas do sistema."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📊 Carregando estatísticas...")

    try:
        stats = await db.get_system_stats()

        text = (
            "📊 *Estatísticas do Sistema*\n\n"
            f"👥 *Usuários Totais:* {stats.get('total_users', 0)}\n"
            f"✅ *Assinaturas Ativas:* {stats.get('active_subscriptions', 0)}\n"
            f"⏳ *Assinaturas Pendentes:* {stats.get('pending_subscriptions', 0)}\n"
            f"❌ *Assinaturas Expiradas:* {stats.get('expired_subscriptions', 0)}\n\n"
            f"💰 *Receita Total:* R$ {stats.get('total_revenue', 0):.2f}\n"
            f"💵 *Receita (Mês):* R$ {stats.get('monthly_revenue', 0):.2f}\n"
            f"💸 *Receita (Hoje):* R$ {stats.get('daily_revenue', 0):.2f}\n\n"
            f"🏢 *Grupos Cadastrados:* {stats.get('total_groups', 0)}\n"
            f"🎟️ *Cupons Ativos:* {stats.get('active_coupons', 0)}\n\n"
            f"📈 *Taxa de Conversão:* {stats.get('conversion_rate', 0):.1f}%\n"
            f"📅 *Última atualização:* {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )

        keyboard = [
            [InlineKeyboardButton("🔄 Atualizar", callback_data="admin_stats")],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Erro ao carregar estatísticas: {e}")
        await query.edit_message_text(
            "❌ Erro ao carregar estatísticas. Tente novamente.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]])
        )

    return SELECTING_ACTION

# --- NOVA FUNCIONALIDADE: GERENCIAR GRUPOS ---
@admin_only
async def manage_groups_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra opções de gerenciamento de grupos."""
    query = update.callback_query
    await query.answer()

    groups = await db.get_all_groups_with_names()

    text = f"🏢 *Gerenciamento de Grupos*\n\n📊 Total de grupos: {len(groups)}\n\n"

    if groups:
        text += "Grupos cadastrados:\n"
        for i, group in enumerate(groups[:10], 1):  # Mostra até 10 grupos
            text += f"{i}. {group.get('name', 'Sem nome')} (`{group['telegram_chat_id']}`)\n"
        if len(groups) > 10:
            text += f"\n... e mais {len(groups) - 10} grupos"

    keyboard = [
        [InlineKeyboardButton("➕ Adicionar Grupo", callback_data="group_add")],
        [InlineKeyboardButton("🗑️ Remover Grupo", callback_data="group_remove")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return MANAGING_GROUPS

# --- NOVA FUNCIONALIDADE: CRIAR CUPOM ---
@admin_only
async def create_coupon_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o processo de criação de cupom."""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🎟️ *Criar Novo Cupom de Desconto*\n\n"
        "Envie o código do cupom (ex: PROMO10, DESCONTO20):\n\n"
        "⚠️ Use apenas letras maiúsculas e números.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return GETTING_COUPON_CODE

@admin_only
async def create_coupon_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o código do cupom."""
    code = update.message.text.strip().upper()

    # Validação básica
    if not code.isalnum() or len(code) < 3:
        await update.message.reply_text(
            "❌ Código inválido. Use apenas letras e números (mínimo 3 caracteres)."
        )
        return GETTING_COUPON_CODE

    # Verifica se já existe
    existing = await db.get_coupon_by_code(code)
    if existing:
        await update.message.reply_text(
            "❌ Este código já existe. Escolha outro."
        )
        return GETTING_COUPON_CODE

    context.user_data['coupon_code'] = code

    await update.message.reply_text(
        f"✅ Código: *{code}*\n\n"
        "Agora envie o valor do desconto:\n"
        "• Para porcentagem: 10% ou 20%\n"
        "• Para valor fixo: R$5 ou R$10",
        parse_mode=ParseMode.MARKDOWN
    )
    return GETTING_COUPON_DISCOUNT

@admin_only
async def create_coupon_get_discount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o valor do desconto e cria o cupom."""
    discount_text = update.message.text.strip()
    code = context.user_data.get('coupon_code')

    try:
        # Parse do desconto
        if '%' in discount_text:
            discount_type = 'percentage'
            discount_value = float(discount_text.replace('%', '').strip())
            if discount_value <= 0 or discount_value > 100:
                raise ValueError("Porcentagem inválida")
        elif 'R$' in discount_text or 'r$' in discount_text.lower():
            discount_type = 'fixed'
            discount_value = float(discount_text.lower().replace('r$', '').strip())
            if discount_value <= 0:
                raise ValueError("Valor inválido")
        else:
            raise ValueError("Formato inválido")

        # Cria o cupom
        coupon = await db.create_coupon(code, discount_type, discount_value)

        if coupon:
            discount_display = f"{discount_value}%" if discount_type == 'percentage' else f"R$ {discount_value:.2f}"
            await update.message.reply_text(
                f"✅ *Cupom criado com sucesso!*\n\n"
                f"🎟️ *Código:* `{code}`\n"
                f"💰 *Desconto:* {discount_display}\n"
                f"📅 *Criado em:* {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
                "Os usuários podem usar /cupom para aplicá-lo.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("❌ Erro ao criar cupom. Tente novamente.")

    except ValueError as e:
        await update.message.reply_text(
            f"❌ Erro de formato. Use, por exemplo: `10%` ou `R$5`"
        )
        return GETTING_COUPON_DISCOUNT

    context.user_data.clear()
    return ConversationHandler.END

# --- NOVA FUNCIONALIDADE: VER LOGS ---
@admin_only
async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra os logs recentes do sistema."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📝 Carregando logs...")

    try:
        logs = await db.get_recent_logs(limit=20)

        if not logs:
            text = "📝 *Logs do Sistema*\n\nNenhum log recente encontrado."
        else:
            text = "📝 *Logs Recentes do Sistema*\n\n"
            for log in logs:
                timestamp = format_date_br(log.get('created_at'))
                log_type = log.get('type', 'info').upper()
                message = log.get('message', 'N/A')
                text += f"🕐 {timestamp}\n📌 [{log_type}] {message}\n\n"

        keyboard = [
            [InlineKeyboardButton("🔄 Atualizar", callback_data="admin_view_logs")],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Erro ao carregar logs: {e}")
        await query.edit_message_text(
            "❌ Erro ao carregar logs.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]])
        )

    return SELECTING_ACTION

# --- NOVA FUNCIONALIDADE: BUSCAR TRANSAÇÕES ---
@admin_only
async def search_transactions_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia busca de transações."""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "💳 *Buscar Transações*\n\n"
        "Envie:\n"
        "• ID do usuário\n"
        "• @username\n"
        "• ID do pagamento MP\n"
        "• 'hoje' para transações de hoje\n"
        "• 'semana' para últimos 7 dias",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return GETTING_TRANSACTION_SEARCH

@admin_only
async def search_transactions_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Executa a busca de transações."""
    search_term = update.message.text.strip().lower()

    await update.message.reply_text("🔍 Buscando transações...")

    try:
        transactions = await db.search_transactions(search_term)

        if not transactions:
            await update.message.reply_text("❌ Nenhuma transação encontrada.")
            return ConversationHandler.END

        text = f"💳 *Resultados da Busca*\n\nEncontradas {len(transactions)} transação(ões):\n\n"

        for trans in transactions[:10]:  # Limita a 10
            user = trans.get('user', {})
            product = trans.get('product', {})
            status_emoji = "✅" if trans['status'] == 'active' else "⏳" if trans['status'] == 'pending_payment' else "❌"

            text += (
                f"{status_emoji} *{user.get('first_name', 'N/A')}* (@{user.get('username', 'N/A')})\n"
                f"   💰 {product.get('name', 'N/A')}\n"
                f"   📅 {format_date_br(trans.get('created_at'))}\n"
                f"   🆔 `{trans.get('mp_payment_id', 'N/A')}`\n\n"
            )

        if len(transactions) > 10:
            text += f"... e mais {len(transactions) - 10} transações"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Erro ao buscar transações: {e}")
        await update.message.reply_text("❌ Erro ao buscar transações.")

    return ConversationHandler.END

# --- FLUXO: CHECAR USUÁRIO (mantido do original) ---
@admin_only
async def check_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text="🔍 Por favor, envie o ID numérico ou o @username do usuário que deseja checar.",
        reply_markup=reply_markup
    )
    return GETTING_USER_ID_FOR_CHECK

@admin_only
async def check_user_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)

    if not user_data:
        await update.message.reply_text("❌ Usuário não encontrado. Tente novamente ou use /cancel.")
        return GETTING_USER_ID_FOR_CHECK

    first_name = user_data.get('first_name', 'N/A')
    tg_id = user_data.get('telegram_user_id', 'N/A')
    username = f"@{user_data['username']}" if user_data.get('username') else 'N/A'
    created_at = format_date_br(user_data.get('created_at'))

    message = (
        f"📊 *Status do Usuário*\n\n"
        f"👤 *Nome:* {first_name}\n"
        f"🆔 *Telegram ID:* `{tg_id}`\n"
        f"✏️ *Username:* {username}\n"
        f"📅 *Cadastro:* {created_at}\n\n"
        "-------------------\n"
    )

    active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)

    if active_sub:
        product_name = active_sub.get('product', {}).get('name', 'N/A')
        start_date = format_date_br(active_sub.get('start_date'))
        end_date = "Vitalício" if not active_sub.get('end_date') else format_date_br(active_sub.get('end_date'))
        mp_id = active_sub.get('mp_payment_id', 'N/A')

        message += (
            f"✅ *Assinatura Ativa*\n"
            f"📦 *Plano:* {product_name}\n"
            f"📅 *Início:* {start_date}\n"
            f"📆 *Fim:* {end_date}\n"
            f"🆔 *ID Pagamento:* `{mp_id}`"
        )
    else:
        # Verifica histórico
        all_subs = user_data.get('subscriptions', [])
        if all_subs:
            message += f"❌ *Nenhuma assinatura ativa*\n\n📜 Histórico: {len(all_subs)} assinatura(s) anterior(es)"
        else:
            message += "❌ *Nenhuma assinatura encontrada*"

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(
        "Para checar outro usuário, envie um novo ID/username.\nPara voltar ao menu, use /admin."
    )
    return ConversationHandler.END

# --- FLUXO: CONCEDER ACESSO (mantido com melhorias) ---
@admin_only
async def grant_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text="✅ Envie o ID numérico ou @username do usuário para conceder acesso.",
        reply_markup=reply_markup
    )
    return GETTING_USER_ID_FOR_GRANT

@admin_only
async def grant_access_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)

    if not user_data:
        await update.message.reply_text(
            "❌ Usuário não encontrado. Peça para o usuário iniciar o bot primeiro com /start."
        )
        return ConversationHandler.END

    active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)

    if active_sub:
        await update.message.reply_text(
            "⚠️ Este usuário já possui uma assinatura ativa!\n\n"
            "💡 Opções:\n"
            "• Use 'Revogar Acesso' primeiro\n"
            "• Ou conceda um novo plano para estender a assinatura atual"
        )
        # Permite continuar para estender

    context.user_data['grant_user_id'] = user_data['id']
    context.user_data['grant_telegram_user_id'] = user_data['telegram_user_id']

    keyboard = [
        [InlineKeyboardButton("📅 Assinatura Mensal", callback_data=f"grant_plan_{PRODUCT_ID_MONTHLY}")],
        [InlineKeyboardButton("💎 Acesso Vitalício", callback_data=f"grant_plan_{PRODUCT_ID_LIFETIME}")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "✅ Usuário encontrado. Qual plano deseja conceder?",
        reply_markup=reply_markup
    )
    return SELECTING_PLAN_FOR_GRANT

@admin_only
async def grant_access_select_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split('_')[-1])
    db_user_id = context.user_data.get('grant_user_id')
    telegram_user_id = context.user_data.get('grant_telegram_user_id')
    admin_id = update.effective_user.id

    await query.edit_message_text(text="⏳ Processando concessão...")

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique_grant_id = f"manual_grant_by_admin_{admin_id}_{timestamp}"

    new_sub = await db.create_manual_subscription(db_user_id, product_id, unique_grant_id)

    if new_sub:
        # Registra o log
        await db.create_log(
            'admin_action',
            f"Admin {admin_id} concedeu acesso manual para usuário {telegram_user_id}"
        )

        await send_access_links(context.bot, telegram_user_id, new_sub.get('mp_payment_id', 'manual'))
        await query.edit_message_text(
            text=f"✅ Acesso concedido com sucesso para o usuário {telegram_user_id}!\n\n"
            f"📬 Os links foram enviados automaticamente."
        )

        try:
            await context.bot.send_message(
                telegram_user_id,
                "🎉 Boas notícias! Um administrador concedeu acesso premium a você.\n\n"
                "Seus links de convite foram enviados acima. Bem-vindo(a)!"
            )
        except Exception as e:
            logger.error(f"Erro ao notificar usuário {telegram_user_id}: {e}")
    else:
        await query.edit_message_text(text="❌ Falha ao conceder acesso. Verifique os logs.")

    context.user_data.clear()
    return ConversationHandler.END

# --- FLUXO: REVOGAR ACESSO (mantido do original) ---
@admin_only
async def revoke_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text="❌ Envie o ID numérico ou @username do usuário que terá o acesso revogado.",
        reply_markup=reply_markup
    )
    return GETTING_USER_ID_FOR_REVOKE

@admin_only
async def revoke_access_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)

    if not user_data:
        await update.message.reply_text("❌ Usuário não encontrado. Tente novamente.")
        return GETTING_USER_ID_FOR_REVOKE

    active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)

    if not active_sub:
        await update.message.reply_text("Este usuário não possui uma assinatura ativa para revogar.")
        return ConversationHandler.END

    context.user_data['revoke_db_user_id'] = user_data['id']
    context.user_data['revoke_telegram_user_id'] = user_data['telegram_user_id']

    keyboard = [
        [InlineKeyboardButton("✅ SIM, REVOGAR AGORA", callback_data="revoke_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"⚠️ *ATENÇÃO* ⚠️\n\n"
        f"Você está prestes a revogar o acesso de *{user_data['first_name']}* (`{user_data['telegram_user_id']}`) "
        f"e removê-lo(a) de todos os grupos.\n\n"
        f"Esta ação é irreversível. Confirma?",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return CONFIRMING_REVOKE

@admin_only
async def revoke_access_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Processando revogação...")

    db_user_id = context.user_data.get('revoke_db_user_id')
    telegram_user_id = context.user_data.get('revoke_telegram_user_id')
    admin_id = update.effective_user.id

    success = await db.revoke_subscription(db_user_id, f"revoked_by_admin_{admin_id}")

    if success:
        # Registra o log
        await db.create_log(
            'admin_action',
            f"Admin {admin_id} revogou acesso do usuário {telegram_user_id}"
        )

        removed_count = await scheduler.kick_user_from_all_groups(telegram_user_id, context.bot)
        await query.edit_message_text(
            f"✅ Acesso revogado com sucesso!\n\n"
            f"👤 Usuário: {telegram_user_id}\n"
            f"🚫 Removido de {removed_count} grupo(s)"
        )

        try:
            await context.bot.send_message(
                telegram_user_id,
                "⚠️ Seu acesso foi revogado por um administrador.\n\n"
                "Para mais informações, use /suporte."
            )
        except Exception as e:
            logger.error(f"Erro ao notificar usuário {telegram_user_id}: {e}")
    else:
        await query.edit_message_text("❌ Falha ao revogar o acesso no banco de dados.")

    context.user_data.clear()
    return ConversationHandler.END

# --- FLUXO: BROADCAST (mantido do original) ---
@admin_only
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text="📢 Envie a mensagem que você deseja enviar a todos os usuários com assinatura ativa.\n\n"
        "Você pode enviar texto, imagens, vídeos ou documentos.\nUse /cancel para abortar.",
        reply_markup=reply_markup
    )
    return GETTING_BROADCAST_MESSAGE

@admin_only
async def broadcast_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['broadcast_message'] = update.message

    keyboard = [
        [InlineKeyboardButton("✅ SIM, ENVIAR AGORA", callback_data="broadcast_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📋 Esta é a mensagem que será enviada.\n\n"
        "⚠️ Será enviada para TODOS os usuários com assinatura ativa.\n\n"
        "Você confirma o envio?",
        reply_markup=reply_markup
    )
    return CONFIRMING_BROADCAST

@admin_only
async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    message_to_send = context.user_data.get('broadcast_message')
    if not message_to_send:
        await query.edit_message_text("❌ Erro: Mensagem não encontrada. Operação cancelada.")
        return ConversationHandler.END

    await query.edit_message_text("📊 Buscando usuários... O envio começará em breve.")

    user_ids = await db.get_all_active_tg_user_ids()
    total_users = len(user_ids)

    await query.edit_message_text(
        f"📤 Iniciando envio para {total_users} usuários...\n\n"
        f"⏱️ Tempo estimado: ~{total_users // 20} minutos\n\n"
        f"Você receberá uma notificação quando concluir."
    )

    # Registra o log
    await db.create_log(
        'admin_action',
        f"Admin {update.effective_user.id} iniciou broadcast para {total_users} usuários"
    )

    asyncio.create_task(
        run_broadcast(context, message_to_send, user_ids, query.message.chat_id, query.message.message_id)
    )

    context.user_data.clear()
    return ConversationHandler.END

async def run_broadcast(context: ContextTypes.DEFAULT_TYPE, message_to_send, user_ids, admin_chat_id, admin_message_id):
    """Executa o broadcast com controle de rate limit aprimorado."""
    sent_count, failed_count, blocked_count = 0, 0, 0
    total_users = len(user_ids)
    start_time = datetime.now()

    for i, user_id in enumerate(user_ids):
        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=message_to_send.chat_id,
                message_id=message_to_send.message_id
            )
            sent_count += 1

            # Atualiza progresso a cada 50 mensagens
            if i % 50 == 0 and i > 0:
                elapsed = (datetime.now() - start_time).seconds
                estimated_total = (elapsed / i) * total_users if i > 0 else 0
                remaining = estimated_total - elapsed

                await context.bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=admin_message_id,
                    text=f"📊 Progresso: {i}/{total_users}\n"
                    f"✅ Enviados: {sent_count}\n"
                    f"❌ Falhas: {failed_count}\n"
                    f"🚫 Bloqueados: {blocked_count}\n\n"
                    f"⏱️ Tempo restante: ~{int(remaining // 60)} min"
                )
                await asyncio.sleep(3)  # Pausa para evitar limites
            else:
                await asyncio.sleep(0.5)

        except RetryAfter as e:
            logger.warning(f"Rate limit atingido. Pausando por {e.retry_after}s")
            await context.bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=admin_message_id,
                text=f"⏸️ Limite da API atingido.\n\n"
                f"Pausando por {e.retry_after} segundos...\n"
                f"Progresso: {i}/{total_users}"
            )
            await asyncio.sleep(e.retry_after)
            # Tenta reenviar
            try:
                await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=message_to_send.chat_id,
                    message_id=message_to_send.message_id
                )
                sent_count += 1
            except (BadRequest, Forbidden):
                failed_count += 1

        except Forbidden:
            blocked_count += 1
        except BadRequest:
            failed_count += 1
        except Exception as e:
            logger.error(f"Erro inesperado no broadcast para {user_id}: {e}")
            failed_count += 1

    elapsed_time = (datetime.now() - start_time).seconds

    final_text = (
        f"📢 *Broadcast Concluído!*\n\n"
        f"📊 *Estatísticas:*\n"
        f"✅ Enviados: {sent_count}\n"
        f"🚫 Bloquearam o bot: {blocked_count}\n"
        f"❌ Outras falhas: {failed_count}\n\n"
        f"⏱️ Tempo total: {elapsed_time // 60} min {elapsed_time % 60}s\n"
        f"📈 Taxa de sucesso: {(sent_count/total_users*100):.1f}%"
    )

    await context.bot.edit_message_text(
        chat_id=admin_chat_id,
        message_id=admin_message_id,
        text=final_text,
        parse_mode=ParseMode.MARKDOWN
    )

    # Registra o resultado
    await db.create_log(
        'broadcast_complete',
        f"Broadcast concluído: {sent_count}/{total_users} enviados"
    )

# --- FLUXO: ENVIAR LINK DE NOVO GRUPO (mantido do original) ---
@admin_only
async def grant_new_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📊 Buscando grupos cadastrados...")

    groups = await db.get_all_groups_with_names()

    if not groups:
        await query.edit_message_text(
            "❌ Nenhum grupo encontrado no banco de dados.\n\n"
            "Use 'Gerenciar Grupos' para cadastrar um grupo primeiro.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")
            ]])
        )
        return ConversationHandler.END

    keyboard = []
    for group in groups:
        group_name = group.get('name', f"ID: {group['telegram_chat_id']}")
        keyboard.append([
            InlineKeyboardButton(
                f"📁 {group_name}",
                callback_data=f"new_group_select_{group['telegram_chat_id']}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "✉️ *Enviar Link de Novo Grupo*\n\n"
        "Selecione o grupo para o qual deseja enviar convites a todos os assinantes ativos:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return SELECTING_NEW_GROUP

@admin_only
async def grant_new_group_select_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = int(query.data.split('_')[-1])
    context.user_data['new_group_chat_id'] = chat_id

    try:
        chat = await context.bot.get_chat(chat_id)
        group_name = chat.title
    except Exception as e:
        logger.error(f"Não foi possível obter informações do grupo {chat_id}: {e}")
        group_name = f"ID {chat_id}"

    keyboard = [
        [InlineKeyboardButton("✅ SIM, ENVIAR CONVITES", callback_data="new_group_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"⚠️ *CONFIRMAÇÃO* ⚠️\n\n"
        f"Você está prestes a enviar um convite para o grupo:\n"
        f"📁 *{group_name}*\n\n"
        f"Será enviado para *TODOS* os assinantes ativos.\n"
        f"O bot verificará e *não enviará* o link para quem já for membro.\n\n"
        f"Deseja continuar?"
    )

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return CONFIRMING_NEW_GROUP_BROADCAST

@admin_only
async def grant_new_group_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = context.user_data.get('new_group_chat_id')
    if not chat_id:
        await query.edit_message_text("❌ Erro: ID do grupo não encontrado. Operação cancelada.")
        return ConversationHandler.END

    await query.edit_message_text("📊 Buscando usuários ativos... O envio começará em breve.")

    user_ids = await db.get_all_active_tg_user_ids()
    total_users = len(user_ids)

    if total_users == 0:
        await query.edit_message_text("❌ Nenhum usuário com assinatura ativa foi encontrado.")
        return ConversationHandler.END

    await query.edit_message_text(
        f"📤 Iniciando envio de convites para {total_users} usuários...\n\n"
        f"⏱️ Isso pode levar alguns minutos."
    )

    # Registra o log
    await db.create_log(
        'admin_action',
        f"Admin {update.effective_user.id} iniciou envio de links do grupo {chat_id} para {total_users} usuários"
    )

    asyncio.create_task(
        run_new_group_broadcast(context, chat_id, user_ids, query.message.chat_id, query.message.message_id)
    )

    context.user_data.clear()
    return ConversationHandler.END

async def run_new_group_broadcast(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_ids: list[int],
    admin_chat_id: int,
    admin_message_id: int
):
    """Envia convites de grupo com verificação de membros e rate limit."""
    sent_count = 0
    failed_count = 0
    already_member_count = 0
    total_users = len(user_ids)
    start_time = datetime.now()

    try:
        chat = await context.bot.get_chat(chat_id)
        group_name = chat.title
    except Exception:
        group_name = f"o grupo (ID: {chat_id})"

    for i, user_id in enumerate(user_ids):
        try:
            # Verifica se já é membro
            member_status = (await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)).status
            if member_status in ['member', 'administrator', 'creator']:
                already_member_count += 1
                continue

            # Gera e envia o link
            link = await context.bot.create_chat_invite_link(chat_id=chat_id, member_limit=1)
            message = (
                f"✨ *Novo Grupo Disponível!*\n\n"
                f"Como nosso assinante, você ganhou acesso ao grupo:\n"
                f"📁 *{group_name}*\n\n"
                f"Clique no link abaixo para entrar:\n"
                f"{link.invite_link}\n\n"
                f"⚠️ Este convite é pessoal e expira em breve!"
            )

            await context.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            sent_count += 1

            # Atualiza progresso
            if i % 30 == 0 and i > 0:
                elapsed = (datetime.now() - start_time).seconds
                await context.bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=admin_message_id,
                    text=f"📊 Progresso: {i}/{total_users}\n"
                    f"✅ Convites enviados: {sent_count}\n"
                    f"👤 Já eram membros: {already_member_count}\n"
                    f"❌ Falhas: {failed_count}\n\n"
                    f"⏱️ Tempo decorrido: {elapsed // 60}m {elapsed % 60}s"
                )
                await asyncio.sleep(3)
            else:
                await asyncio.sleep(0.5)

        except RetryAfter as e:
            logger.warning(f"Rate limit no broadcast de grupo. Pausando {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
        except (BadRequest, Forbidden):
            failed_count += 1
        except Exception as e:
            logger.error(f"Erro ao processar usuário {user_id} para grupo {chat_id}: {e}")
            failed_count += 1

    elapsed_time = (datetime.now() - start_time).seconds
    denominator = total_users - already_member_count
    success_rate = (sent_count / denominator * 100) if denominator > 0 else 0

    final_text = (
        f"✉️ *Envio de Convites Concluído!*\n\n"
        f"📁 *Grupo:* {group_name}\n"
        f"👥 *Total de assinantes:* {total_users}\n"
        f"-----------------------------------\n"
        f"✅ *Convites enviados:* {sent_count}\n"
        f"👤 *Já eram membros:* {already_member_count}\n"
        f"❌ *Falhas:* {failed_count}\n\n"
        f"⏱️ *Tempo total:* {elapsed_time // 60}m {elapsed_time % 60}s\n"
        f"📈 *Taxa de sucesso:* {success_rate:.1f}%"
    )

    await context.bot.edit_message_text(
        chat_id=admin_chat_id,
        message_id=admin_message_id,
        text=final_text,
        parse_mode=ParseMode.MARKDOWN
    )

# --- CANCELAR E CONVERSATION HANDLER ---
@admin_only
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = "❌ Operação cancelada."

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text)
    elif update.message:
        await update.message.reply_text(text)

    context.user_data.clear()
    return ConversationHandler.END

def get_admin_conversation_handler() -> ConversationHandler:
    """Retorna o ConversationHandler aprimorado com todas as funcionalidades."""
    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_panel)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(view_stats, pattern="^admin_stats$"),
                CallbackQueryHandler(check_user_start, pattern="^admin_check_user$"),
                CallbackQueryHandler(grant_access_start, pattern="^admin_grant_access$"),
                CallbackQueryHandler(revoke_access_start, pattern="^admin_revoke_access$"),
                CallbackQueryHandler(broadcast_start, pattern="^admin_broadcast$"),
                CallbackQueryHandler(grant_new_group_start, pattern="^admin_grant_new_group$"),
                CallbackQueryHandler(manage_groups_start, pattern="^admin_manage_groups$"),
                CallbackQueryHandler(create_coupon_start, pattern="^admin_create_coupon$"),
                CallbackQueryHandler(view_logs, pattern="^admin_view_logs$"),
                CallbackQueryHandler(search_transactions_start, pattern="^admin_transactions$"),
                CallbackQueryHandler(cancel, pattern="^admin_cancel$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
            ],
            GETTING_USER_ID_FOR_CHECK: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_user_receive_id)
            ],
            GETTING_USER_ID_FOR_GRANT: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, grant_access_receive_id)
            ],
            SELECTING_PLAN_FOR_GRANT: [
                CallbackQueryHandler(grant_access_select_plan, pattern="^grant_plan_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            GETTING_USER_ID_FOR_REVOKE: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, revoke_access_receive_id)
            ],
            CONFIRMING_REVOKE: [
                CallbackQueryHandler(revoke_access_confirm, pattern="^revoke_confirm$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            GETTING_BROADCAST_MESSAGE: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND, broadcast_receive_message)
            ],
            CONFIRMING_BROADCAST: [
                CallbackQueryHandler(broadcast_confirm, pattern="^broadcast_confirm$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            SELECTING_NEW_GROUP: [
                CallbackQueryHandler(grant_new_group_select_group, pattern="^new_group_select_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            CONFIRMING_NEW_GROUP_BROADCAST: [
                CallbackQueryHandler(grant_new_group_confirm, pattern="^new_group_confirm$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            MANAGING_GROUPS: [
                # Adicionar handlers específicos para add/remove/rename grupos aqui
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
            ],
            GETTING_COUPON_CODE: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_coupon_get_code)
            ],
            GETTING_COUPON_DISCOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_coupon_get_discount)
            ],
            GETTING_TRANSACTION_SEARCH: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_transactions_execute)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("admin", admin_panel)
        ],
        per_user=True,
        per_chat=True,
    )
