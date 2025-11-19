# --- START OF FILE scheduler.py (VERSÃO CORRIGIDA E COMPLETA) ---

import os
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.constants import ParseMode

import db_supabase as db

# --- CONSTANTES DE PRODUTO ---
TRIAL_PRODUCT_ID = int(os.getenv("TRIAL_PRODUCT_ID", 3))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger("Scheduler")
load_dotenv()

# Carrega as mesmas variáveis de ambiente
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TIMEZONE_BR = timezone(timedelta(hours=-3))

# --- FUNÇÃO REUTILIZÁVEL ---
async def kick_user_from_all_groups(user_id: int, bot: Bot):
    """Expulsa e desbane um usuário de todos os grupos listados no DB."""
    supabase_client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    groups_response = await asyncio.to_thread(
        lambda: supabase_client.table('groups').select('telegram_chat_id').execute()
    )
    group_ids = [g['telegram_chat_id'] for g in groups_response.data]

    if not group_ids:
        logger.error(f"CRÍTICO: [kick_user] Nenhum grupo encontrado no DB. Não é possível remover {user_id}.")
        return 0

    removed_count = 0
    for group_id in group_ids:
        try:
            await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
            await bot.unban_chat_member(chat_id=group_id, user_id=user_id, only_if_banned=True)
            logger.info(f"[kick_user] Usuário {user_id} removido do grupo {group_id}.")
            removed_count += 1
        except Forbidden:
            logger.warning(f"[kick_user] Sem permissão para remover {user_id} do grupo {group_id}.")
        except BadRequest as e:
            # --- LÓGICA APRIMORADA AQUI ---
            error_text = str(e).lower()
            if "user not found" in error_text or "member not found" in error_text:
                logger.info(f"[kick_user] Usuário {user_id} já não estava no grupo {group_id}.")
            elif "can't remove chat owner" in error_text:
                logger.warning(f"[kick_user] Não é possível remover o usuário {user_id} do grupo {group_id} porque ele é o proprietário.")
            else:
                logger.error(f"[kick_user] Erro do Telegram ao remover {user_id} do {group_id}: {e}")
            # --- FIM DA LÓGICA APRIMORADA ---

    return removed_count

# --- FUNÇÕES DO SCHEDULER ---

async def find_and_process_expiring_subscriptions(supabase: Client, bot: Bot):
    """Encontra assinaturas que estão para vencer e envia UM ÚNICO aviso COM BOTÕES."""
    try:
        three_days_from_now = (datetime.now(TIMEZONE_BR) + timedelta(days=3)).isoformat()

        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id, user:users(telegram_user_id), end_date, product:products(name)')
            .eq('status', 'active')
            .eq('expiry_warning_sent', False)
            .lte('end_date', three_days_from_now)
            .execute()
        )

        if not response.data:
            logger.info("Nenhuma nova assinatura encontrada para enviar aviso de vencimento.")
            return

        logger.info(f"Encontradas {len(response.data)} assinaturas para enviar aviso de vencimento.")

        for sub in response.data:
            user_id = sub.get('user', {}).get('telegram_user_id')
            sub_id = sub.get('id')
            product_name = sub.get('product', {}).get('name', 'Seu plano')

            if not user_id or not sub_id:
                continue

            end_date = datetime.fromisoformat(sub['end_date'])
            days_left = (end_date - datetime.now(TIMEZONE_BR)).days
            end_date_br = end_date.astimezone(TIMEZONE_BR).strftime('%d/%m/%Y às %H:%M')

            # Mensagem personalizada com emojis
            if days_left == 0:
                time_text = "HOJE"
                emoji = "🚨"
            elif days_left == 1:
                time_text = "AMANHÃ"
                emoji = "⚠️"
            else:
                time_text = f"em {days_left} dias"
                emoji = "⏰"

            message = (
                f"{emoji} *Aviso de Vencimento* {emoji}\n\n"
                f"Olá! Sua assinatura do plano *{product_name}* está próxima de vencer.\n\n"
                f"📅 *Vence {time_text}* ({end_date_br})\n\n"
                f"Para continuar aproveitando todo o conteúdo exclusivo, "
                f"renove sua assinatura agora mesmo! ✨"
            )

            # NOVO: Adicionar botões para facilitar a renovação
            keyboard = [
                [InlineKeyboardButton("🔄 Renovar Agora", callback_data='menu_view_plans')],
                [InlineKeyboardButton("📊 Ver Minha Assinatura", callback_data='menu_subscription_status')],
                [InlineKeyboardButton("🏠 Menu Principal", callback_data='menu_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=message,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
                logger.info(f"Aviso de vencimento enviado para o usuário {user_id} (assinatura {sub_id}).")

                # Marca o aviso como enviado
                await asyncio.to_thread(
                    lambda: supabase.table('subscriptions')
                    .update({'expiry_warning_sent': True})
                    .eq('id', sub_id)
                    .execute()
                )
                logger.info(f"Assinatura {sub_id} marcada como 'aviso enviado'.")

                await asyncio.sleep(0.1)

            except RetryAfter as e:
                logger.warning(f"Rate limit atingido ao enviar aviso para {user_id}. Aguardando {e.retry_after} segundos.")
                await asyncio.sleep(e.retry_after)
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=message,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    await asyncio.to_thread(
                        lambda: supabase.table('subscriptions')
                        .update({'expiry_warning_sent': True})
                        .eq('id', sub_id)
                        .execute()
                    )
                    logger.info(f"Aviso enviado e assinatura {sub_id} marcada após retry.")
                except Exception as e_inner:
                    logger.error(f"Falha ao reenviar aviso para {user_id} após retry: {e_inner}")
            except (Forbidden, BadRequest):
                logger.warning(f"Não foi possível enviar aviso para o usuário {user_id} (bloqueou o bot?). Marcando como enviado.")
                await asyncio.to_thread(
                    lambda: supabase.table('subscriptions')
                    .update({'expiry_warning_sent': True})
                    .eq('id', sub_id)
                    .execute()
                )
    except Exception as e:
        logger.error(f"Erro ao processar avisos de expiração: {e}", exc_info=True)


async def find_and_process_expired_subscriptions(supabase: Client, bot: Bot):
    """Encontra assinaturas vencidas, remove os usuários e envia mensagem COM BOTÕES."""
    try:
        now_iso = datetime.now(TIMEZONE_BR).isoformat()

        expired_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id, product_id, user:users(telegram_user_id)')
            .eq('status', 'active')
            .lt('end_date', now_iso)
            .execute()
        )

        if not expired_response.data:
            logger.info("Nenhuma assinatura vencida encontrada.")
            return

        logger.info(f"Encontradas {len(expired_response.data)} assinaturas vencidas para processar.")

        for sub in expired_response.data:
            user_id = sub.get('user', {}).get('telegram_user_id')
            sub_id = sub.get('id')
            product_id = sub.get('product_id')

            if not user_id:
                continue

            logger.info(f"Processando expiração para o usuário {user_id} (assinatura {sub_id}).")

            # Remove dos grupos
            removed_count = await kick_user_from_all_groups(user_id, bot)

            # Marca como expirada
            await asyncio.to_thread(
                lambda: supabase.table('subscriptions').update({'status': 'expired'}).eq('id', sub_id).execute()
            )
            logger.info(f"Assinatura {sub_id} do usuário {user_id} marcada como 'expired'. Removido de {removed_count} grupos.")

            # Mensagens personalizadas com botões
            try:
                if product_id == TRIAL_PRODUCT_ID:
                    # Mensagem para fim da degustação
                    product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
                    product_lifetime = await db.get_product_by_id(PRODUCT_ID_LIFETIME)

                    text = (
                        "✨ *Seu período de degustação acabou!*\n\n"
                        "Esperamos que você tenha gostado do conteúdo! 🎉\n\n"
                        "Para continuar aproveitando nossos grupos exclusivos, "
                        "escolha um dos planos abaixo:"
                    )

                    keyboard = [
                        [InlineKeyboardButton(
                            f"📅 Mensal - R$ {product_monthly['price']:.2f}",
                            callback_data=f'pay_{PRODUCT_ID_MONTHLY}'
                        )],
                        [InlineKeyboardButton(
                            f"💎 Vitalício - R$ {product_lifetime['price']:.2f}",
                            callback_data=f'pay_{PRODUCT_ID_LIFETIME}'
                        )],
                        [InlineKeyboardButton(
                            "📋 Ver Todos os Planos",
                            callback_data='menu_view_plans'
                        )],
                        [InlineKeyboardButton(
                            "🏠 Menu Principal",
                            callback_data='menu_main'
                        )]
                    ]
                else:
                    # Mensagem para assinatura paga expirada
                    text = (
                        "⏰ *Sua assinatura expirou*\n\n"
                        "Sentiremos sua falta! Para voltar a ter acesso aos nossos "
                        "grupos exclusivos, renove sua assinatura agora mesmo."
                    )

                    keyboard = [
                        [InlineKeyboardButton(
                            "🔄 Renovar Assinatura",
                            callback_data='menu_view_plans'
                        )],
                        [InlineKeyboardButton(
                            "📊 Ver Status",
                            callback_data='menu_subscription_status'
                        )],
                        [InlineKeyboardButton(
                            "🏠 Menu Principal",
                            callback_data='menu_main'
                        )]
                    ]

                reply_markup = InlineKeyboardMarkup(keyboard)
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )

            except (Forbidden, BadRequest):
                logger.warning(f"Não foi possível notificar o usuário {user_id} sobre a expiração.")
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem de expiração para {user_id}: {e}")

    except Exception as e:
        logger.error(f"Erro CRÍTICO no processo de expiração: {e}", exc_info=True)

